INSERT OVERWRITE TABLE daily_rollup
SELECT sale_day, region, COUNT(*), SUM(order_total), CURRENT_TIMESTAMP()
FROM raw_orders
GROUP BY sale_day, region;

INSERT OVERWRITE TABLE fact_sales
PARTITION (sale_day='${hivevar:run_date}', region)
SELECT order_id, customer_id, primary_product_id, order_total, region
FROM raw_orders
WHERE order_status = 'COMPLETE'
DISTRIBUTE BY customer_id
SORT BY order_id;

LOAD DATA LOCAL INPATH '/tmp/orders.csv' INTO TABLE stage_orders;

FROM raw_orders
INSERT OVERWRITE TABLE agg_sales
  SELECT region, sale_day, COUNT(*), SUM(order_total) GROUP BY region, sale_day
INSERT INTO TABLE audit_events
  SELECT order_id, 'orders', CAST(order_id AS STRING), 'loaded', CURRENT_TIMESTAMP();
