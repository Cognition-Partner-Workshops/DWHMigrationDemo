CREATE DATABASE analytics COMMENT 'Analytics warehouse';
CREATE DATABASE staging COMMENT 'Landing and staging zone';
USE staging;
USE analytics;
SET hive.exec.dynamic.partition=true;
SET hive.exec.dynamic.partition.mode=nonstrict;
SET hivevar:warehouse=hdfs:///warehouse;
ADD JAR hdfs:///libs/normalize-udf-2.1.jar;
CREATE TEMPORARY FUNCTION normalize AS 'com.example.Normalize'
  USING JAR 'hdfs:///libs/normalize-udf-2.1.jar';

CREATE TABLE raw_customers (
  customer_id BIGINT, first_name STRING, last_name STRING, email STRING,
  region STRING, created_at TIMESTAMP
);
CREATE EXTERNAL TABLE raw_orders (
  order_id BIGINT, customer_id BIGINT, primary_product_id BIGINT,
  order_total DECIMAL(12,2), order_status STRING, region STRING
)
PARTITIONED BY (sale_day STRING)
CLUSTERED BY (customer_id) SORTED BY (order_id DESC) INTO 16 BUCKETS
ROW FORMAT DELIMITED FIELDS TERMINATED BY '\001'
STORED AS ORC
LOCATION '${hivevar:warehouse}/raw_orders'
TBLPROPERTIES ('owner'='commerce', 'compression'='zlib');
CREATE TABLE raw_order_lines (
  order_id BIGINT, line_number INT, product_id BIGINT, quantity INT,
  unit_price DECIMAL(12,2)
);
CREATE TABLE raw_products (
  product_id BIGINT, sku STRING, product_name STRING, category STRING,
  list_price DECIMAL(12,2), active BOOLEAN
);
CREATE TABLE raw_payments (
  payment_id BIGINT, order_id BIGINT, payment_method STRING,
  amount DECIMAL(12,2), paid_at TIMESTAMP
);
CREATE TABLE raw_returns (
  return_id BIGINT, order_id BIGINT, return_reason STRING, returned_at TIMESTAMP
);
CREATE TABLE raw_events (
  event_id BIGINT, event_ts TIMESTAMP, event_name STRING, tags ARRAY<STRING>,
  payload MAP<STRING,STRING>
);
CREATE TABLE raw_shipments (
  shipment_id BIGINT, order_id BIGINT, carrier STRING, tracking_number STRING,
  shipped_at TIMESTAMP
);
CREATE TABLE raw_addresses (
  customer_id BIGINT, address_type STRING, city STRING, state STRING,
  postal_code STRING
);
CREATE TABLE raw_inventory (
  product_id BIGINT, warehouse_id BIGINT, on_hand INT, reserved INT,
  snapshot_at TIMESTAMP
);
CREATE TABLE raw_discounts (
  discount_id BIGINT, product_id BIGINT, discount_pct DECIMAL(5,2),
  starts_on DATE, ends_on DATE
);
CREATE TABLE raw_currencies (
  currency_code STRING, currency_name STRING, usd_rate DECIMAL(12,6),
  effective_date DATE
);
CREATE TABLE raw_regions (
  region_id BIGINT, region_name STRING, country_code STRING, sales_area STRING
);
CREATE TABLE raw_suppliers (
  supplier_id BIGINT, supplier_name STRING, country_code STRING,
  contact_email STRING
);
CREATE TABLE raw_promotions (
  promotion_id BIGINT, promotion_name STRING, channel STRING,
  start_date DATE, end_date DATE
);
CREATE TABLE raw_reviews (
  review_id BIGINT, product_id BIGINT, customer_id BIGINT, rating INT,
  review_text STRING, review_ts TIMESTAMP
);
CREATE TABLE raw_clickstream (
  session_id STRING, customer_id BIGINT, page_url STRING, referrer STRING,
  event_ts TIMESTAMP
);
CREATE TABLE raw_sessions (
  session_id STRING, customer_id BIGINT, started_at TIMESTAMP,
  ended_at TIMESTAMP, device_type STRING
);
CREATE TABLE raw_tax_rates (
  tax_region STRING, jurisdiction STRING, rate DECIMAL(7,5),
  valid_from DATE, valid_to DATE
);
CREATE TABLE raw_refunds (
  refund_id BIGINT, order_id BIGINT, refund_amount DECIMAL(12,2),
  refund_reason STRING, processed_at TIMESTAMP
);

CREATE TABLE dim_customers (
  customer_key BIGINT, customer_id BIGINT, full_name STRING, email STRING,
  region STRING, valid_from DATE, valid_to DATE
);
CREATE TABLE dim_products (
  product_key BIGINT, product_id BIGINT, sku STRING, product_name STRING,
  category STRING, current_flag BOOLEAN
);
CREATE TABLE dim_dates (
  date_key INT, calendar_date DATE, month_name STRING, quarter_number INT,
  fiscal_year INT
);
CREATE TABLE dim_regions (
  region_key INT, region_id BIGINT, region_name STRING, country_code STRING
);
CREATE TABLE dim_stores (
  store_key BIGINT, store_number STRING, store_name STRING,
  region_key INT, opened_on DATE
);
CREATE TABLE dim_suppliers (
  supplier_key BIGINT, supplier_id BIGINT, supplier_name STRING,
  country_code STRING
);
CREATE TABLE dim_currency (
  currency_key INT, currency_code STRING, currency_name STRING,
  usd_rate DECIMAL(12,6)
);
CREATE TABLE dim_status (
  status_key INT, status_code STRING, status_description STRING, active BOOLEAN
);
CREATE TABLE dim_channels (
  channel_key INT, channel_code STRING, channel_name STRING, digital_flag BOOLEAN
);
CREATE TABLE dim_promotion (
  promotion_key BIGINT, promotion_id BIGINT, promotion_name STRING,
  channel_key INT, start_date DATE
);

CREATE TABLE stage_orders (
  order_id BIGINT, customer_id BIGINT, order_total DECIMAL(12,2),
  source_file STRING, landed_at TIMESTAMP
);
CREATE TABLE stage_customers (
  customer_id BIGINT, first_name STRING, last_name STRING, email STRING,
  source_file STRING
);
CREATE TABLE stage_products (
  product_id BIGINT, sku STRING, product_name STRING, category STRING,
  source_file STRING
);
CREATE TABLE stage_payments (
  payment_id BIGINT, order_id BIGINT, amount DECIMAL(12,2),
  payment_method STRING, source_file STRING
);
CREATE TABLE stage_events (
  event_id BIGINT, event_name STRING, event_ts TIMESTAMP,
  payload STRING, source_file STRING
);
CREATE TABLE stage_returns (
  return_id BIGINT, order_id BIGINT, return_reason STRING,
  returned_at TIMESTAMP, source_file STRING
);
CREATE TABLE stage_shipments (
  shipment_id BIGINT, order_id BIGINT, carrier STRING,
  tracking_number STRING, source_file STRING
);
CREATE TABLE stage_inventory (
  product_id BIGINT, warehouse_id BIGINT, on_hand INT, reserved INT,
  source_file STRING
);
CREATE TABLE stage_addresses (
  customer_id BIGINT, address_type STRING, city STRING, state STRING,
  source_file STRING
);
CREATE TABLE stage_discounts (
  discount_id BIGINT, product_id BIGINT, discount_pct DECIMAL(5,2),
  starts_on DATE, source_file STRING
);

CREATE TABLE audit_events (
  event_id BIGINT, entity_name STRING, entity_id STRING, action_name STRING,
  recorded_at TIMESTAMP
);
CREATE TABLE job_runs (
  run_id BIGINT, job_name STRING, started_at TIMESTAMP, finished_at TIMESTAMP,
  run_status STRING
);
CREATE TABLE quality_checks (
  check_id BIGINT, table_name STRING, check_name STRING, failure_count BIGINT,
  checked_at TIMESTAMP
);
CREATE TABLE etl_batches (
  batch_id BIGINT, source_name STRING, started_at TIMESTAMP,
  completed_at TIMESTAMP, row_count BIGINT
);
CREATE TABLE data_watermarks (
  pipeline_name STRING, source_name STRING, watermark_value TIMESTAMP,
  updated_at TIMESTAMP
);
