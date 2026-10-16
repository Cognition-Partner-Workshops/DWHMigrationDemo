-- Converted: legacy partitioning and clustering clauses retained
-- Reason: partitioning and liquid clustering cannot be combined here
CREATE TABLE main.sales.partitioned_orders (order_id BIGINT)
USING DELTA
PARTITIONED BY (order_date)
CLUSTER BY (customer_id);
