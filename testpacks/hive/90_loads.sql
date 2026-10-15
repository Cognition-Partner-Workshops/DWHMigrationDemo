INSERT OVERWRITE TABLE daily_rollup
SELECT COUNT(*) FROM raw_orders;

LOAD DATA LOCAL INPATH '/tmp/orders.csv' INTO TABLE stage_orders;

FROM raw_orders
INSERT OVERWRITE TABLE agg_sales SELECT COUNT(*)
INSERT INTO TABLE audit_events SELECT id;
