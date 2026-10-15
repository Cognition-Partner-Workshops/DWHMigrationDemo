-- Converted: orders use a MergeTree engine and explicit sorting key
-- Reason: the primary query path filters and aggregates by customer and date
CREATE TABLE sales.orders
(
  order_id UInt64,
  customer_id UInt64,
  order_date Date,
  amount Decimal(12, 2)
)
ENGINE = MergeTree
ORDER BY (customer_id, order_date);
