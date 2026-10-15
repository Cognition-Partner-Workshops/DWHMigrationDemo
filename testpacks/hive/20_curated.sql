CREATE TABLE fact_sales (
  order_id BIGINT,
  customer_id BIGINT,
  product_id BIGINT,
  amount DECIMAL(12,2)
)
PARTITIONED BY (sale_day STRING, region STRING)
CLUSTERED BY (customer_id) INTO 8 BUCKETS
ROW FORMAT DELIMITED FIELDS TERMINATED BY '\001'
STORED AS ORC
LOCATION '${hivevar:warehouse}/curated/fact_sales'
TBLPROPERTIES ('owner'='finance', 'retention.days'='365');

CREATE TABLE agg_sales AS
SELECT region, sale_day, COUNT(*) AS order_count, SUM(order_total) AS gross_sales
FROM v_customer_orders
GROUP BY region, sale_day;

CREATE TABLE daily_rollup (
  sale_day STRING,
  region STRING,
  order_count BIGINT,
  gross_sales DECIMAL(18,2),
  refreshed_at TIMESTAMP
);

CREATE TABLE broken_table (id INT,);
CREATE TABLE recovery_survivor (run_id BIGINT, recovered_at TIMESTAMP, recovery_note STRING);

CREATE SCHEDULED QUERY unsupported_schedule;
CREATE TABLE post_unsupported (record_id BIGINT, status STRING, created_at TIMESTAMP);

MSCK REPAIR TABLE raw_orders;
ALTER TABLE raw_orders ADD IF NOT EXISTS
  PARTITION (sale_day='2025-01-01') LOCATION '/warehouse/raw_orders/sale_day=2025-01-01'
  PARTITION (sale_day='2025-01-02') LOCATION '/warehouse/raw_orders/sale_day=2025-01-02';
