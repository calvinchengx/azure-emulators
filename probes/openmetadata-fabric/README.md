# OpenMetadata on Fabric: SQL Server connector probe

Can OpenMetadata's **open-source** SQL Server connector catalog a Fabric
Warehouse? Its only Fabric connector ships in Collate, the commercial edition,
so this probe tests the free route before anyone buys that.

It runs the connector's own code, not a copy: its connection builder, its
SQLAlchemy dialect patches, and every query in its `queries` module, all
imported from the pinned `openmetadata-ingestion` release. A connector upgrade
therefore changes what the probe tests, and cannot leave it testing stale SQL.

## Why not against fabric-emulator

fabric-emulator's SQL endpoint runs stock SQL Server 2022 as its engine
([docs/16](https://github.com/calvinchengx/fabric-emulator/blob/main/docs/16-warehouse-tds.md)).
Every catalog query would be answered by SQL Server, so a pass would only
re-prove that OpenMetadata supports SQL Server. The question is about Fabric's
catalog, so the oracle has to be a real Fabric Warehouse.

## What is known (27 September 2026, `openmetadata-ingestion` 2.0.2.0)

| # | Test | Result |
|---|---|---|
| 1 | Service-principal login is expressible | **Pass.** `check_auth_url.py`, offline |
| 2 | The 24 catalog objects exist in Fabric | **Mixed.** Microsoft lists the metadata objects for Fabric Warehouse; `sys.views`, `sys.indexes`, `sys.index_columns`, the key and constraint views, and every plan-cache and Query Store view are not listed |
| 3 | The probe works | **Pass.** Control against SQL Server 2022 finds tables, view, primary key, foreign key, comment and procedure; an empty database fails |
| 4 | It works against real Fabric | **Pending.** Needs a service principal |

Expect the metadata leg to mostly pass and the history leg to fail: Fabric keeps
query history in `queryinsights`, which the connector does not read. Gold
lineage comes from the dbt connector instead, so that does not block it.

## Test 1: service-principal login (offline)

```bash
uv run python check_auth_url.py
```

Builds the connection as OpenMetadata would for
`connectionOptions: {Authentication: ActiveDirectoryServicePrincipal}` with
placeholder credentials, and checks the ODBC string the driver receives. CI runs
it on every change here.

## Test 4: against a real Fabric Warehouse

Needs Microsoft ODBC Driver 18 and a service principal with at least Viewer on
the workspace. Credentials come from the environment and are never printed.

```bash
export FABRIC_SQL_ENDPOINT=<id>.datawarehouse.fabric.microsoft.com
export FABRIC_WAREHOUSE=<warehouse name>
export AZURE_CLIENT_ID=<service principal client id>
export AZURE_CLIENT_SECRET=<secret>
uv run python fabric_probe.py
```

It exits 0 when the metadata leg passes and 1 when any metadata check fails,
including finding no tables to inspect. The history leg is reported but does not
set the exit code. `--dry-run` prints every check without connecting.

If it passes, configure the OpenMetadata service with `scheme: mssql+pyodbc`,
the connection options above, and `ingestAllDatabases: false` (listing all
databases reads `master.sys.databases`), and schedule no usage or query-lineage
workflows for it.

## Test 3: the SQL Server control

Rerun this after bumping `openmetadata-ingestion`, before trusting a Fabric
result from the new version. Any check that passes here and fails on Fabric is
Fabric's gap, not the probe's.

```bash
export SA_PW="Pr0be-$(openssl rand -hex 12)"
docker run -d --name om-probe-mssql --platform linux/amd64 -e ACCEPT_EULA=Y \
  -e MSSQL_SA_PASSWORD="$SA_PW" -p 127.0.0.1:14330:1433 \
  mcr.microsoft.com/mssql/server:2022-latest
docker exec -i om-probe-mssql /opt/mssql-tools18/bin/sqlcmd -C -S localhost -U sa -P "$SA_PW" -b <<'SQL'
CREATE DATABASE gold_warehouse;
GO
USE gold_warehouse;
GO
CREATE TABLE dbo.dim_customer (customer_id int NOT NULL PRIMARY KEY, name varchar(100));
CREATE TABLE dbo.fact_orders (order_id int NOT NULL PRIMARY KEY,
  customer_id int REFERENCES dbo.dim_customer(customer_id), amount decimal(18,2));
GO
CREATE VIEW dbo.v_order_totals AS SELECT customer_id, SUM(amount) AS total FROM dbo.fact_orders GROUP BY customer_id;
GO
CREATE PROCEDURE dbo.refresh_totals AS SELECT COUNT(*) FROM dbo.fact_orders;
GO
EXEC sp_addextendedproperty 'MS_Description', 'Customers', 'SCHEMA', 'dbo', 'TABLE', 'dim_customer';
GO
SQL
PROBE_CONTROL=1 FABRIC_SQL_ENDPOINT=127.0.0.1:14330 FABRIC_WAREHOUSE=gold_warehouse \
  AZURE_CLIENT_ID=sa AZURE_CLIENT_SECRET="$SA_PW" uv run python fabric_probe.py
docker rm -f om-probe-mssql
```

Expected: every check `OK`, `get_foreign_keys(all tables)` and
`MSSQL_GET_FOREIGN_KEY` finding 1, `get_table_comment` returning `Customers`,
and exit 0. Pointing `FABRIC_WAREHOUSE` at an empty database must exit 1.
