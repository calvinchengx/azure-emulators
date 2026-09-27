"""What-if, not a fix: skip the two RisingWave blockers, then rerun the probe.

Blocker 1: the connector's column query adds POSTGRES_COL_IDENTITY, which calls
json_build_object, whenever server_version_info >= (10,). Reporting (9, 6)
removes exactly that subquery.
Blocker 2: SQLAlchemy's domain lookup, which the connector's get_columns calls,
uses pg_collation_is_visible. Returning no domains skips it.

If every required check passes with both skipped, those two are the whole gap.

  uv run python what_if.py risingwave                     # expect exit 0
  uv run python what_if.py risingwave --database empty_db # expect exit 1
"""

import sys

from sqlalchemy.dialects.postgresql.base import PGDialect

import rw_probe

_initialize = PGDialect.initialize


def initialize(self, connection):
    _initialize(self, connection)
    self.server_version_info = (9, 6)


PGDialect.initialize = initialize
PGDialect._load_domains = lambda self, connection, *args, **kw: []

if __name__ == "__main__":
    target = sys.argv[1]
    if "--database" in sys.argv:
        rw_probe.TARGETS[target]["database"] = sys.argv[sys.argv.index("--database") + 1]
    sys.exit(rw_probe.run(target))
