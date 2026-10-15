-- Converted: source index, nested array, and enforced-key forms retained
-- Reason: BigQuery uses clustering and unenforced key metadata instead
CREATE INDEX ix_orders_customer ON sales.orders (customer_id);
SELECT order_id FROM "sales"."orders";
CREATE TABLE sales.bad_orders (
  nested_values ARRAY<ARRAY<INT64>>,
  PRIMARY KEY (order_id),
  order_id INT64
)
CLUSTER BY region, status, channel, source, bucket;
