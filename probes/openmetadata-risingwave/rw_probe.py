"""Probe: can OpenMetadata's Postgres connector catalog RisingWave?

Runs the connector's own code (connection builder, dialect patches, queries and
lineage parser, all imported from the pinned openmetadata-ingestion release)
against a target seeded by seed.py, and asserts that each seeded object is
found with the right shape. Run it against RisingWave and against a PostgreSQL
control: a check that passes on the control and fails on RisingWave is a
RisingWave gap, not a probe bug.

  uv run python rw_probe.py risingwave
  uv run python rw_probe.py postgres
"""

from __future__ import annotations

import sys
import warnings

warnings.filterwarnings("ignore")

from sqlalchemy import inspect, text  # noqa: E402

import metadata.ingestion.source.database.postgres.metadata  # noqa: E402,F401  (applies the connector's dialect patches)
import metadata.ingestion.source.database.postgres.queries as q  # noqa: E402
from metadata.generated.schema.entity.services.connections.database.postgresConnection import (  # noqa: E402
    PostgresConnection as PostgresConnectionConfig,
)
from metadata.ingestion.lineage.models import Dialect  # noqa: E402
from metadata.ingestion.lineage.parser import LineageParser  # noqa: E402
from metadata.ingestion.source.database.postgres.connection import PostgresConnection  # noqa: E402
from metadata.ingestion.source.database.postgres.utils import get_postgres_time_column_name  # noqa: E402

TARGETS = {
    "risingwave": {"hostPort": "127.0.0.1:4566", "username": "root", "database": "dev", "password": None},
    "postgres": {"hostPort": "127.0.0.1:55432", "username": "postgres", "database": "postgres", "password": "probe-control"},
}
SCHEMA = "public"

REQUIRED = "required"  # what ingestion and view lineage need
INFO = "info"  # reported, never sets the exit code


class Probe:
    def __init__(self, engine):
        self.engine = engine
        self.insp = inspect(engine)
        self.rows: list[tuple[str, str, bool | None, str]] = []

    def check(self, leg: str, name: str, fn, expect=None):
        """expect(value) -> bool decides pass; without it, no error is a pass."""
        try:
            value = fn()
            ok = expect(value) if expect else True
            self.rows.append((leg, name, ok, repr(value)[:150]))
            return value
        except Exception as exc:  # report every failure, never stop on one
            self.engine.dispose()
            self.rows.append((leg, name, False, f"{type(exc).__name__}: {' '.join(str(exc).split())[:150]}"))
            return None

    def sql(self, sql, binds=None):
        with self.engine.connect() as conn:
            return [tuple(r) for r in conn.execute(text(sql), binds or {}).fetchall()]


def lineage(view_def: str):
    parser = LineageParser(view_def, dialect=Dialect.POSTGRES)
    sources = sorted(str(t).split(".")[-1] for t in parser.source_tables)
    columns = sorted({f"{str(src).split('.')[-2]}.{str(src).split('.')[-1]}" for src, _ in parser.column_lineage})
    return {"sources": sources, "columns": columns}


def run(target: str) -> int:
    cfg = PostgresConnectionConfig.model_validate(
        {
            "hostPort": TARGETS[target]["hostPort"],
            "username": TARGETS[target]["username"],
            "database": TARGETS[target]["database"],
            "authType": {"password": TARGETS[target]["password"]} if TARGETS[target]["password"] else None,
        }
    )
    engine = PostgresConnection(cfg).client
    p = Probe(engine)
    names = lambda rows: sorted(r[0] for r in rows)  # noqa: E731

    # Metadata ingestion, as the connector does it.
    p.check(REQUIRED, "schemas include public", p.insp.get_schema_names, lambda v: SCHEMA in v)
    tables = p.check(REQUIRED, "table list (POSTGRES_GET_TABLE_NAMES)", lambda: p.sql(q.POSTGRES_GET_TABLE_NAMES, {"schema": SCHEMA}),
                     lambda v: {"customers", "orders"} <= set(names(v)))
    p.check(INFO, "  relkinds in that list", lambda: sorted(tables or []))
    p.check(REQUIRED, "view list has v_big_orders", lambda: p.insp.get_view_names(SCHEMA), lambda v: "v_big_orders" in v)
    p.check(INFO, "  view list has mv_customer_totals", lambda: p.insp.get_view_names(SCHEMA), lambda v: "mv_customer_totals" in v)
    p.check(INFO, "  materialized view list (connector does not call it)", lambda: p.insp.get_materialized_view_names(SCHEMA))
    p.check(REQUIRED, "columns of customers", lambda: [c["name"] for c in p.insp.get_columns("customers", SCHEMA)],
            lambda v: v == ["customer_id", "name"])
    p.check(REQUIRED, "column types of orders", lambda: [str(c["type"]) for c in p.insp.get_columns("orders", SCHEMA)],
            lambda v: len(v) == 3 and v[0] == "INTEGER")
    p.check(REQUIRED, "columns of mv_customer_totals", lambda: [c["name"] for c in p.insp.get_columns("mv_customer_totals", SCHEMA)],
            lambda v: v == ["customer_id", "name", "total"])
    p.check(REQUIRED, "primary key of customers", lambda: p.insp.get_pk_constraint("customers", SCHEMA)["constrained_columns"],
            lambda v: v == ["customer_id"])
    p.check(INFO, "foreign keys of orders (RisingWave has none by design)", lambda: len(p.insp.get_foreign_keys("orders", SCHEMA)))
    p.check(REQUIRED, "table comment of customers", lambda: p.insp.get_table_comment("customers", SCHEMA)["text"],
            lambda v: v == "Customers")
    p.check(REQUIRED, "all table comments (POSTGRES_TABLE_COMMENTS)", lambda: p.sql(q.POSTGRES_TABLE_COMMENTS),
            lambda v: any(r[1] == "customers" and r[2] == "Customers" for r in v))
    p.check(REQUIRED, "schema comments", lambda: len(p.sql(q.POSTGRES_SCHEMA_COMMENTS)))
    p.check(REQUIRED, "table owners", lambda: len(p.sql(q.POSTGRES_TABLE_OWNERS)))
    p.check(REQUIRED, "database list", lambda: names(p.sql(q.POSTGRES_GET_DATABASE)), lambda v: TARGETS[target]["database"] in v)
    p.check(REQUIRED, "server version", lambda: p.sql(q.POSTGRES_GET_SERVER_VERSION)[0][0])
    p.check(REQUIRED, "test: column metadata", lambda: p.sql(q.TEST_COLUMN_METADATA)[0][0])
    p.check(REQUIRED, "test: table comments", lambda: p.sql(q.TEST_TABLE_COMMENTS)[0][0])
    p.check(REQUIRED, "test: information_schema.columns", lambda: p.sql(q.TEST_INFORMATION_SCHEMA_COLUMNS)[0][0])
    p.check(INFO, "row-level policies (tags)", lambda: len(p.sql(q.POSTGRES_GET_ALL_TABLE_PG_POLICY.format(schema_name=SCHEMA, database_name=TARGETS[target]["database"]))))
    p.check(INFO, "partition details", lambda: len(p.sql(q.POSTGRES_PARTITION_DETAILS, {"table_name": "orders", "schema_name": SCHEMA})))
    p.check(INFO, "stored procedures", lambda: len(p.sql(q.POSTGRES_GET_STORED_PROCEDURES.format(schema_name=SCHEMA))))
    p.check(INFO, "functions", lambda: len(p.sql(q.POSTGRES_GET_FUNCTIONS.format(schema_name=SCHEMA))))

    # View lineage: the connector prefixes each definition with "create view <schema>.<name> as".
    defs = p.check(REQUIRED, "view definitions (POSTGRES_VIEW_DEFINITIONS)", lambda: {r[1]: r[2] for r in p.sql(q.POSTGRES_VIEW_DEFINITIONS)},
                   lambda v: {"v_big_orders", "mv_customer_totals"} <= set(v))
    for view, want_sources in (("v_big_orders", ["orders"]), ("mv_customer_totals", ["customers", "orders"])):
        if defs and view in defs:
            p.check(INFO, f"  definition of {view}", lambda view=view: defs[view])
            p.check(REQUIRED, f"lineage sources of {view}", lambda view=view: lineage(defs[view])["sources"],
                    lambda v, w=want_sources: v == w)
            p.check(REQUIRED, f"column lineage of {view}", lambda view=view: lineage(defs[view])["columns"], lambda v: len(v) > 0)
        else:
            p.rows.append((REQUIRED, f"lineage of {view}", False, "no definition returned"))

    # Query history, used for usage and query lineage.
    column = p.check(INFO, "pg_stat_statements time column", lambda: get_postgres_time_column_name(engine))
    p.check(INFO, "query history (pg_stat_statements)",
            lambda: len(p.sql(q.POSTGRES_TEST_GET_QUERIES.format(time_column_name=column or "total_exec_time", query_statement_source="pg_stat_statements"))))

    print(f"== {target}: {p.sql('SELECT version()')[0][0][:70]} ==")
    width = max(len(n) for _, n, _, _ in p.rows)
    for leg, name, ok, detail in p.rows:
        mark = "PASS" if ok else ("FAIL" if leg == REQUIRED else "note")
        print(f"{mark:4}  {name:<{width}}  {detail}")
    failed = [n for leg, n, ok, _ in p.rows if leg == REQUIRED and not ok]
    print(f"\nrequired: {'PASS' if not failed else 'FAIL (' + '; '.join(failed) + ')'}")
    return 0 if not failed else 1


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in TARGETS:
        print(f"usage: rw_probe.py {{{'|'.join(TARGETS)}}}", file=sys.stderr)
        sys.exit(2)
    sys.exit(run(sys.argv[1]))
