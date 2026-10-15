CREATE FILE FORMAT analytics.raw.ff_csv TYPE = CSV FIELD_DELIMITER = ',' SKIP_HEADER = 1;
CREATE STAGE analytics.raw.stage_ingest URL = 's3://synthetic-data/inbound'
  FILE_FORMAT = (FORMAT_NAME = analytics.raw.ff_csv);
CREATE STREAM analytics.raw.stream_orders ON TABLE analytics.raw.orders;
CREATE PIPE analytics.raw.pipe_orders AUTO_INGEST = TRUE AS
  COPY INTO analytics.raw.orders FROM @analytics.raw.stage_ingest/orders.csv
  FILE_FORMAT = (FORMAT_NAME = analytics.raw.ff_csv);
CREATE TASK analytics.curated.task_stage WAREHOUSE analytics.wh_etl SCHEDULE = '5 minutes' AS
  COPY INTO analytics.raw.events FROM @analytics.raw.stage_ingest/events.csv;
CREATE TASK analytics.curated.task_orders WAREHOUSE analytics.wh_etl
  AFTER analytics.curated.task_stage
  WHEN SYSTEM$STREAM_HAS_DATA('analytics.raw.stream_orders') AS
  INSERT INTO analytics.curated.agg_daily_sales
  SELECT customer_id, SUM(amount) FROM analytics.raw.orders GROUP BY customer_id;
CREATE PROCEDURE analytics.curated.p_refresh(x NUMBER) RETURNS NUMBER
  LANGUAGE JAVASCRIPT HANDLER = 'run' AS $$return x;$$;
CREATE FUNCTION analytics.curated.fn_tax(x NUMBER) RETURNS NUMBER
  LANGUAGE SQL AS $$SELECT x * 0.1$$;
