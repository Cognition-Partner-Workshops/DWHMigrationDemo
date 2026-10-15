-- Converted: staged rows are appended in a single insert-select operation
-- Reason: a single insert block is atomic for the MergeTree table
INSERT INTO sales.orders (order_id, customer_id, order_date, amount)
SELECT order_id, customer_id, order_date, amount
FROM sales.staged_orders
WHERE load_date = today();
