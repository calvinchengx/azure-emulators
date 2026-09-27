"""Seed the same logical objects into RisingWave and a PostgreSQL control."""
import sys, time, psycopg2

COMMON = [
    "CREATE TABLE customers (customer_id int PRIMARY KEY, name varchar)",
    "CREATE TABLE orders (order_id int PRIMARY KEY, customer_id int, amount decimal)",
    "COMMENT ON TABLE customers IS 'Customers'",
    "INSERT INTO customers VALUES (1, 'Ada'), (2, 'Lin')",
    "INSERT INTO orders VALUES (10, 1, 150), (11, 2, 40)",
    "CREATE VIEW v_big_orders AS SELECT order_id, amount FROM orders WHERE amount > 100",
    "CREATE MATERIALIZED VIEW mv_customer_totals AS SELECT c.customer_id, c.name, SUM(o.amount) AS total "
    "FROM customers c JOIN orders o ON c.customer_id = o.customer_id GROUP BY c.customer_id, c.name",
]
RISINGWAVE_ONLY = [
    "CREATE SOURCE src_events (event_id int, payload varchar) WITH (connector = 'datagen', "
    "fields.event_id.kind = 'sequence', fields.event_id.start = '1', fields.event_id.end = '10', "
    "datagen.rows.per.second = '1') FORMAT PLAIN ENCODE JSON",
    "CREATE SINK sink_totals FROM mv_customer_totals WITH (connector = 'blackhole')",
]
POSTGRES_ONLY = [
    "ALTER TABLE orders ADD FOREIGN KEY (customer_id) REFERENCES customers (customer_id)",
]

def connect(dsn):
    for _ in range(60):
        try:
            return psycopg2.connect(dsn)
        except psycopg2.OperationalError:
            time.sleep(2)
    raise SystemExit(f"could not connect: {dsn.split('password')[0]}")

target = sys.argv[1]
if target == "risingwave":
    conn, extra = connect("host=127.0.0.1 port=4566 user=root dbname=dev"), RISINGWAVE_ONLY
else:
    conn, extra = connect("host=127.0.0.1 port=55432 user=postgres password=probe-control dbname=postgres"), POSTGRES_ONLY
conn.autocommit = True
with conn.cursor() as cur:
    for stmt in COMMON[:4] + (POSTGRES_ONLY if target != "risingwave" else []) + COMMON[4:] + (RISINGWAVE_ONLY if target == "risingwave" else []):
        cur.execute(stmt)
    if target == "risingwave":
        cur.execute("FLUSH")
    cur.execute("SELECT version()")
    print(target, "seeded:", cur.fetchone()[0][:80])
