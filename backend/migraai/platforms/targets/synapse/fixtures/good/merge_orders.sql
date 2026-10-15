-- Converted: matched order updates
-- Reason: the source is a table rather than a table value constructor and has no MERGE hint
MERGE INTO sales.orders AS target
USING stage.orders_load AS source
ON target.order_id = source.order_id
WHEN MATCHED THEN UPDATE SET total_amount = 0;
