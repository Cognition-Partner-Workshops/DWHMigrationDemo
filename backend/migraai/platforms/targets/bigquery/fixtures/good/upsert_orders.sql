-- Converted: source rows are deduplicated before the target merge
-- Reason: one latest source row per order avoids multiple source matches
MERGE `analytics-prod.sales.orders` AS target
USING (
  SELECT order_id, customer_id, order_date, amount
  FROM `analytics-prod.stage.order_updates`
  QUALIFY ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY updated_at DESC) = 1
) AS source
ON target.order_id = source.order_id
WHEN MATCHED THEN UPDATE SET
  customer_id = source.customer_id,
  order_date = source.order_date,
  amount = source.amount
WHEN NOT MATCHED THEN INSERT (order_id, customer_id, order_date, amount)
VALUES (source.order_id, source.customer_id, source.order_date, source.amount);
