-- Converted: merge source is not deduplicated
-- Reason: duplicate source keys can make a BigQuery merge fail
MERGE `analytics-prod.sales.orders` AS target
USING (SELECT order_id, amount FROM `analytics-prod.stage.order_updates`) AS source
ON target.order_id = source.order_id
WHEN MATCHED THEN UPDATE SET amount = source.amount;
