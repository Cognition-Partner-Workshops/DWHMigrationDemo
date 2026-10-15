-- Converted: staged order merge
-- Reason: the MERGE statement must be rewritten without this unsupported CTE form
WITH changed_orders AS (
    SELECT order_id, total_amount FROM stage_orders
)
MERGE INTO sales.orders AS target
USING changed_orders AS source
ON target.order_id = source.order_id
WHEN MATCHED THEN UPDATE SET total_amount = source.total_amount;
