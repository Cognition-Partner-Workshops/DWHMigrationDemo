COPY INTO analytics.raw.orders FROM @analytics.raw.stage_ingest/orders.csv
  FILES = ('orders.csv') FILE_FORMAT = (FORMAT_NAME = analytics.raw.ff_csv);

MERGE INTO analytics.curated.agg_daily_sales target
USING analytics.raw.orders source ON target.customer_id = source.customer_id
WHEN MATCHED THEN UPDATE SET total = source.amount
WHEN NOT MATCHED THEN INSERT (customer_id, total) VALUES (source.customer_id, source.amount);

INSERT INTO analytics.curated.agg_return_rates
SELECT order_id, COUNT(*) FROM analytics.raw.order_lines GROUP BY order_id;
