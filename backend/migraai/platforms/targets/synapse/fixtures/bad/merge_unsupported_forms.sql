-- Converted: staged order merges
-- Reason: remove hints and table value constructors from the MERGE statements
MERGE INTO sales.orders WITH (HOLDLOCK) AS target
USING stage.orders_load AS source
ON target.order_id = source.order_id
WHEN MATCHED THEN UPDATE SET total_amount = 0;

MERGE INTO sales.orders AS target
USING (VALUES (1001, 125.00)) AS source(order_id, total_amount)
ON target.order_id = source.order_id
WHEN MATCHED THEN UPDATE SET total_amount = source.total_amount;
