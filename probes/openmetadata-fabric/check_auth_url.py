"""Offline check: the ODBC string OpenMetadata's SQL Server connector hands the driver
for a Fabric service-principal config. Placeholder credentials only; no network.

  uv run python check_auth_url.py     # exits 0 when every check passes
"""
import warnings; warnings.filterwarnings("ignore")
from sqlalchemy.engine import make_url
from sqlalchemy.dialects.mssql.pyodbc import dialect as PyodbcDialect
from metadata.generated.schema.entity.services.connections.database.mssqlConnection import MssqlConnection
from metadata.ingestion.source.database.mssql.connection import get_connection_url

cfg = MssqlConnection.model_validate({
    "scheme": "mssql+pyodbc",
    "driver": "ODBC Driver 18 for SQL Server",
    "hostPort": "PLACEHOLDER.datawarehouse.fabric.microsoft.com:1433",
    "database": "gold_warehouse",
    "username": "CLIENT-ID-PLACEHOLDER",
    "password": "SECRET-PLACEHOLDER",
    "connectionOptions": {"Authentication": "ActiveDirectoryServicePrincipal", "Encrypt": "yes"},
})
url = get_connection_url(cfg)
print("URL:", str(url).replace("SECRET-PLACEHOLDER", "***"))
args, kwargs = PyodbcDialect().create_connect_args(make_url(url))
print("ODBC:", args[0].replace("SECRET-PLACEHOLDER", "***"))
odbc = dict(p.split("=", 1) for p in args[0].split(";") if "=" in p)
checks = {
    "Authentication=ActiveDirectoryServicePrincipal": odbc.get("Authentication") == "ActiveDirectoryServicePrincipal",
    "UID is the client id": odbc.get("UID") == "CLIENT-ID-PLACEHOLDER",
    "PWD is the secret": odbc.get("PWD") == "SECRET-PLACEHOLDER",
    "Encrypt=yes": odbc.get("Encrypt") == "yes",
    "Database set": odbc.get("Database") == "gold_warehouse",
}
for k, v in checks.items(): print(("PASS " if v else "FAIL ") + k)
raise SystemExit(0 if all(checks.values()) else 1)
