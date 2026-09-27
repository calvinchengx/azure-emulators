# OpenMetadata on RisingWave: Postgres connector probe

Can OpenMetadata's Postgres connector catalog RisingWave, which speaks the
Postgres protocol? OpenMetadata has no RisingWave connector, so this is the only
free route.

The probe runs the connector's own code: its connection builder, its SQLAlchemy
dialect patches, its queries and its lineage parser, all imported from the
pinned `openmetadata-ingestion` release. Each required check asserts that a
seeded object is found with the right shape, not merely that a query ran.

## Findings (27 September 2026)

RisingWave 3.1.0, `openmetadata-ingestion` 2.0.2.0, PostgreSQL 17.11 as control.

**The connector cannot catalog RisingWave, because of three gaps in
RisingWave's Postgres compatibility.** Reading the columns of any table or
materialized view fails:

1. The connector's identity-column subquery (`POSTGRES_COL_IDENTITY`) calls
   `json_build_object`. RisingWave has only `jsonb_build_object`.
2. The same subquery casts to `oid` (`::regclass::oid`). RisingWave has no
   `oid` type.
3. SQLAlchemy's domain lookup, which the connector's `get_columns` calls, uses
   `pg_collation_is_visible`, which RisingWave lacks.

Everything else the connector needs works, including lineage: OpenMetadata's
parser reads RisingWave's view and materialized view definitions and finds the
right source tables and columns.

**Two small changes close the gap.** Simulated exactly on RisingWave 3.1.0 and
PostgreSQL 17.11:

- OpenMetadata: in `POSTGRES_COL_IDENTITY`, use `jsonb_build_object` and drop
  the `::oid` cast. The subquery only runs on PostgreSQL 10+, which all have
  `jsonb_build_object`, and `regclass` compares with `oid` without a cast.
- RisingWave: bind `pg_collation_is_visible` to `true`, as it already does for
  `pg_type_is_visible`. `pg_collation` is empty in RisingWave, so the stub
  cannot give a wrong answer.

With both, every required check passes on RisingWave; with either alone, the
three column checks still fail. On PostgreSQL the changed query returns the same
identity-column details, only with keys in `jsonb` order.

An earlier version of this README said two missing functions were the whole
gap. `what_if.py` skips the entire identity subquery, which hid the `oid` cast
inside it; simulating the actual changes exposed it.

Also found, and not RisingWave-specific:

- **Materialized views are never listed as entities, on PostgreSQL too.** The
  connector's view list uses SQLAlchemy 2's `get_view_names`, which excludes
  them.
- RisingWave sources and sinks have relation kinds `s` and `k`; the connector
  lists only `r`, `p` and `f`, so it never sees them.
- Query history needs `pg_stat_statements`, which RisingWave does not have.

Upstream: [risingwavelabs/risingwave#27049](https://github.com/risingwavelabs/risingwave/issues/27049)
asks for OpenMetadata support and
[open-metadata/OpenMetadata#34060](https://github.com/open-metadata/OpenMetadata/issues/34060)
for a native connector. Neither named these gaps when this was written.

## Files

| File | Does |
|---|---|
| `seed.py` | Seeds the same tables, comment, view and materialized view into both targets; RisingWave also gets a source and a sink |
| `rw_probe.py` | The probe. `uv run python rw_probe.py risingwave` or `postgres` |
| `what_if.py` | The probe with the failing code paths skipped; `--database empty_db` for the negative control |
| `check_findings.py` | Runs all four scenarios and asserts each one's exact set of failing checks |

## Run it

```bash
docker run -d --name om-probe-rw -p 127.0.0.1:4566:4566 \
  risingwavelabs/risingwave:v3.1.0@sha256:ee2e8e7a10b728d01b0e149d2a4f35864f0f52a0c889fa6cf9bc32c8ff46c602 single_node
docker run -d --name om-probe-pg -e POSTGRES_PASSWORD=probe-control \
  -p 127.0.0.1:55432:5432 postgres:17.11
uv run python seed.py risingwave
uv run python seed.py postgres
uv run python check_findings.py
docker rm -f om-probe-rw om-probe-pg
```

`check_findings.py` exits 0 while the findings hold. CI runs the same steps when
this directory changes.

**When it goes red, read it before fixing it.** A red run after bumping
RisingWave or `openmetadata-ingestion` most likely means the gap has closed: the
`risingwave` scenario stops failing its three column checks. Rerun the probe,
then update `FINDINGS`, this README, and the "RisingWave test" section of the
data contracts doc together.
