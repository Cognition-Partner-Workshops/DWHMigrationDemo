-- Converted: Delta table with an explicit catalog, schema, and identity key
-- Reason: Databricks tables are governed through Unity Catalog
CREATE TABLE main.sales.orders (
  order_id BIGINT GENERATED ALWAYS AS IDENTITY,
  customer_id STRING,
  order_date DATE,
  amount DECIMAL(12, 2)
)
USING DELTA
CLUSTER BY (customer_id);
