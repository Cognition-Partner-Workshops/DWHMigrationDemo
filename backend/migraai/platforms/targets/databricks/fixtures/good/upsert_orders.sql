-- Converted: source orders are merged into the Delta target
-- Reason: the load is repeatable and updates existing order values
MERGE INTO main.sales.orders AS target
USING (
  SELECT order_id, customer_id, order_date, amount
  FROM main.stage.order_updates
) AS source
ON target.order_id = source.order_id
WHEN MATCHED THEN UPDATE SET
  customer_id = source.customer_id,
  order_date = source.order_date,
  amount = source.amount
WHEN NOT MATCHED THEN INSERT (order_id, customer_id, order_date, amount)
VALUES (source.order_id, source.customer_id, source.order_date, source.amount);
