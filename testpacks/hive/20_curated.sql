CREATE TABLE fact_sales (
  id BIGINT,
  amount DECIMAL(12, 2)
)
PARTITIONED BY (sale_day STRING)
STORED AS ORC;

CREATE TABLE agg_sales AS
SELECT order_id FROM v_customer_orders;

CREATE TABLE daily_rollup (order_count BIGINT);

CREATE TABLE broken_table (id INT,);
CREATE TABLE recovery_survivor (id BIGINT);

CREATE SCHEDULED QUERY unsupported_schedule;
