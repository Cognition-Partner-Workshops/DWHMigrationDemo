-- Converted: Hive-style storage declaration retained for review
-- Reason: this table needs a Delta format and three-part name
CREATE TABLE sales.legacy_orders (order_id BIGINT)
STORED AS PARQUET;
