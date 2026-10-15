-- Converted: source identity declarations retained for review
-- Reason: Databricks uses a BIGINT identity without source seed syntax
CREATE TABLE main.sales.identity_orders (
  order_id BIGINT IDENTITY(1, 1),
  item_id INT GENERATED ALWAYS AS IDENTITY
)
USING DELTA;
