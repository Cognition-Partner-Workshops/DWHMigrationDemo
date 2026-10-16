-- Converted: duplicate source rows are reduced before the table merge
-- Reason: the latest update for each order is applied deterministically
MERGE INTO ANALYTICS.SALES.ORDERS AS target
USING (
  SELECT order_id, customer_id, order_date, details
  FROM ANALYTICS.STAGE.ORDER_UPDATES
  QUALIFY ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY updated_at DESC) = 1
) AS source
ON target.order_id = source.order_id
WHEN MATCHED THEN UPDATE SET
  customer_id = source.customer_id,
  order_date = source.order_date,
  details = source.details
WHEN NOT MATCHED THEN INSERT (order_id, customer_id, order_date, details)
VALUES (source.order_id, source.customer_id, source.order_date, source.details);
