"""Probe: can OpenMetadata's open-source SQL Server connector catalog a Fabric Warehouse?

Runs the connector's own code against a real Fabric SQL endpoint: its connection
builder, its SQLAlchemy dialect patches, and every query in its queries module,
imported from the installed package rather than copied, so the probe cannot drift
from the connector.

Credentials come from the environment and are never printed:
  FABRIC_SQL_ENDPOINT   <id>.datawarehouse.fabric.microsoft.com
  FABRIC_WAREHOUSE      the warehouse (database) name
  AZURE_CLIENT_ID       service principal client id
  AZURE_CLIENT_SECRET   service principal secret

  uv run python fabric_probe.py            # live run
  uv run python fabric_probe.py --dry-run  # print the plan, touch nothing
"""

from __future__ import annotations

import os
import sys
import warnings
from datetime import datetime, timedelta, timezone

warnings.filterwarnings("ignore")

from sqlalchemy import inspect, text  # noqa: E402

import metadata.ingestion.source.database.mssql.metadata  # noqa: E402,F401  (applies the connector's dialect patches)
import metadata.ingestion.source.database.mssql.queries as q  # noqa: E402
from metadata.generated.schema.entity.services.connections.database.mssqlConnection import (  # noqa: E402
    MssqlConnection,
)
from metadata.ingestion.connections.builders import (  # noqa: E402
    create_generic_db_connection,
    get_connection_args_common,
)
from metadata.ingestion.source.database.mssql.connection import MssqlChecks, get_connection_url  # noqa: E402

# Metadata ingestion needs these; lineage for the warehouse comes from dbt instead.
METADATA = "metadata"
# Query-history lineage and usage; Fabric keeps history in queryinsights instead.
HISTORY = "history"

# The connector's own list, plus Fabric's query-history schema; compared case-insensitively.
SYSTEM_SCHEMAS = {s.lower() for s in MssqlChecks.SYSTEM_SCHEMAS} | {"queryinsights"}


def now_window() -> dict:
    end = datetime.now(timezone.utc)
    return {
        "start_time": (end - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
        "end_time": end.strftime("%Y-%m-%d %H:%M:%S"),
        "result_limit": 5,
        "filters": "",
        "start_date": (end - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
    }


def sql_checks(database: str, schema: str | None, table: str | None) -> list[tuple[str, str, str, dict]]:
    """(leg, name, sql, binds) for every query constant the connector ships."""
    w = now_window()
    fmt_extra = {"database_name": database, "schema_name": schema or "dbo"}
    leg = {
        "MSSQL_GET_CURRENT_DATABASE": METADATA,
        "MSSQL_GET_DATABASE": METADATA,
        "MSSQL_GET_TABLE_COMMENTS": METADATA,
        "MSSQL_GET_DATABASE_COMMENTS": METADATA,
        "MSSQL_GET_SCHEMA_COMMENTS": METADATA,
        "MSSQL_GET_STORED_PROCEDURE_COMMENTS": METADATA,
        "MSSQL_ALL_VIEW_DEFINITIONS": METADATA,
        "MSSQL_GET_FOREIGN_KEY": METADATA,
        "MSSQL_GET_STORED_PROCEDURES": METADATA,
        "MSSQL_GET_ENCRYPTED_STORED_PROCEDURES": METADATA,
        "GET_DB_CONFIGS": METADATA,
    }
    checks = []
    for name in sorted(dir(q)):
        value = getattr(q, name)
        if not (name.isupper() and isinstance(value, str)):
            continue
        sql = value
        if "{" in sql:
            sql = sql.format(**w, **fmt_extra)
        binds = {}
        if ":owner" in sql:
            binds = {"owner": schema or "dbo", "tablename": table or ""}
        if ":schema_name" in sql:
            binds = {"schema_name": schema or "dbo"}
        checks.append((leg.get(name, HISTORY), name, sql, binds))
    return checks


def run(dry_run: bool) -> int:
    missing = [v for v in ("FABRIC_SQL_ENDPOINT", "FABRIC_WAREHOUSE", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET") if not os.environ.get(v)]
    if missing and not dry_run:
        print("missing environment variables: " + ", ".join(missing), file=sys.stderr)
        return 2
    database = os.environ.get("FABRIC_WAREHOUSE", "<warehouse>")

    if dry_run:
        for leg, name, sql, _ in sql_checks(database, "dbo", "<table>"):
            print(f"[{leg}] {name}: {' '.join(sql.split())[:110]}")
        print("[metadata] inspector: schemas, tables, views, columns, pk, fks, unique, view definition, comment")
        return 0

    # PROBE_CONTROL=1 points the same checks at a plain SQL Server with a SQL login,
    # proving the probe itself works and giving the baseline Fabric is compared with.
    control = os.environ.get("PROBE_CONTROL") == "1"
    options = (
        {"Encrypt": "yes", "TrustServerCertificate": "yes"}
        if control
        else {"Authentication": "ActiveDirectoryServicePrincipal", "Encrypt": "yes"}
    )
    config = MssqlConnection.model_validate(
        {
            "scheme": "mssql+pyodbc",
            "driver": os.environ.get("ODBC_DRIVER", "ODBC Driver 18 for SQL Server"),
            "hostPort": os.environ["FABRIC_SQL_ENDPOINT"] if ":" in os.environ["FABRIC_SQL_ENDPOINT"] else f"{os.environ['FABRIC_SQL_ENDPOINT']}:1433",
            "database": database,
            "username": os.environ["AZURE_CLIENT_ID"],
            "password": os.environ["AZURE_CLIENT_SECRET"],
            "connectionOptions": options,
        }
    )
    engine = create_generic_db_connection(
        connection=config,
        get_connection_url_fn=get_connection_url,
        get_connection_args_fn=get_connection_args_common,
    )

    results: list[tuple[str, str, str]] = []

    def record(leg: str, name: str, fn):
        try:
            outcome = fn()
            results.append((leg, name, f"OK {outcome}"))
            return outcome
        except Exception as exc:  # the probe reports every failure, it never stops on one
            message = " ".join(str(exc).split())[:160]
            results.append((leg, name, f"ERROR {type(exc).__name__}: {message}"))
            return None

    insp = inspect(engine)
    schemas = record(METADATA, "inspector.get_schema_names", insp.get_schema_names) or []
    user_schemas = [s for s in schemas if s.lower() not in SYSTEM_SCHEMAS]
    schema = user_schemas[0] if user_schemas else "dbo"
    tables = record(METADATA, f"inspector.get_table_names({schema})", lambda: insp.get_table_names(schema=schema)) or []
    views = record(METADATA, f"inspector.get_view_names({schema})", lambda: insp.get_view_names(schema=schema)) or []
    table = tables[0] if tables else None
    if table:
        record(METADATA, f"inspector.get_columns({table})", lambda: [c["name"] for c in insp.get_columns(table, schema=schema)])
        record(METADATA, f"inspector.get_pk_constraint({table})", lambda: insp.get_pk_constraint(table, schema=schema))
        # Every table, since a foreign key usually sits on a fact table, not the first one listed.
        record(METADATA, "inspector.get_foreign_keys(all tables)", lambda: sum(len(insp.get_foreign_keys(t, schema=schema)) for t in tables))
        record(METADATA, f"inspector.get_table_comment({table})", lambda: insp.get_table_comment(table, schema=schema))
    else:
        results.append((METADATA, "inspector per-table checks", f"ERROR nothing to inspect: no tables in schema {schema}"))
    if views:
        record(METADATA, f"inspector.get_view_definition({views[0]})", lambda: bool(insp.get_view_definition(views[0], schema=schema)))

    for leg, name, sql, binds in sql_checks(database, schema, table):
        per_table = "tablename" in binds and len(tables) > 1

        def execute(sql=sql, binds=binds, per_table=per_table):
            with engine.connect() as conn:
                if per_table:
                    count = sum(len(conn.execute(text(sql), {**binds, "tablename": t}).fetchall()) for t in tables)
                else:
                    count = len(conn.execute(text(sql), binds).fetchall())
            return f"rows={count}"
        record(leg, name + (" (all tables)" if per_table else ""), execute)

    width = max(len(n) for _, n, _ in results)
    for leg in (METADATA, HISTORY):
        print(f"\n== {leg} leg ==")
        for l, name, outcome in results:
            if l == leg:
                print(f"{name:<{width}}  {outcome}")

    metadata_failures = [n for l, n, o in results if l == METADATA and o.startswith("ERROR")]
    print(f"\nmetadata leg: {'PASS' if not metadata_failures else 'FAIL (' + ', '.join(metadata_failures) + ')'}")
    print("history leg: informational; the connector falls back from Query Store to plan-cache DMVs, and Fabric documents neither")
    return 0 if not metadata_failures else 1


if __name__ == "__main__":
    sys.exit(run(dry_run="--dry-run" in sys.argv))
