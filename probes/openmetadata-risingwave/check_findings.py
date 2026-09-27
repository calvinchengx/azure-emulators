"""Assert the recorded findings, so a change in either project shows up as a red run.

Runs four scenarios against databases already started and seeded (README) and
compares each one's exact set of failing required checks with what was found on
27 September 2026. Matching the set, not just the exit code, stops a different
failure passing itself off as the known one.

When RisingWave or OpenMetadata closes the gap, the risingwave scenario stops
failing and this goes red on purpose: update FINDINGS and the doc it came from.
"""

import re
import subprocess
import sys

import psycopg2

COLUMN_CHECKS = {"columns of customers", "column types of orders", "columns of mv_customer_totals"}
EMPTY_DB_CHECKS = COLUMN_CHECKS | {
    "table list (POSTGRES_GET_TABLE_NAMES)",
    "view list has v_big_orders",
    "primary key of customers",
    "table comment of customers",
    "all table comments (POSTGRES_TABLE_COMMENTS)",
    "view definitions (POSTGRES_VIEW_DEFINITIONS)",
    "lineage of v_big_orders",
    "lineage of mv_customer_totals",
}

# scenario: (argv, the exact set of failing required checks expected)
FINDINGS = {
    "postgres control": (["rw_probe.py", "postgres"], set()),
    "risingwave": (["rw_probe.py", "risingwave"], COLUMN_CHECKS),
    "risingwave, failing code paths skipped": (["what_if.py", "risingwave"], set()),
    "risingwave empty database, failing code paths skipped": (["what_if.py", "risingwave", "--database", "empty_db"], EMPTY_DB_CHECKS),
}


def failing(output: str) -> set[str]:
    match = re.search(r"^required: FAIL \((.*)\)$", output, re.MULTILINE)
    if match:
        return set(match.group(1).split("; "))
    if re.search(r"^required: PASS$", output, re.MULTILINE):
        return set()
    raise RuntimeError("no verdict line in the probe output")


def main() -> int:
    conn = psycopg2.connect("host=127.0.0.1 port=4566 user=root dbname=dev")
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT datname FROM pg_database")
        if "empty_db" not in {r[0] for r in cur.fetchall()}:
            cur.execute("CREATE DATABASE empty_db")

    ok = True
    for scenario, (argv, expected) in FINDINGS.items():
        run = subprocess.run([sys.executable, "-W", "ignore", *argv], capture_output=True, text=True, check=False)
        try:
            got = failing(run.stdout)
        except RuntimeError as exc:
            print(f"FAIL  {scenario}: {exc}\n{run.stdout[-2000:]}{run.stderr[-2000:]}")
            ok = False
            continue
        exit_ok = run.returncode == (1 if expected else 0)
        if got == expected and exit_ok:
            print(f"PASS  {scenario}: failing required checks as recorded ({len(got)})")
        else:
            ok = False
            print(f"FAIL  {scenario}: exit {run.returncode}")
            print(f"      unexpected failures: {sorted(got - expected)}")
            print(f"      expected but passing: {sorted(expected - got)}")
            print(run.stdout[-3000:])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
