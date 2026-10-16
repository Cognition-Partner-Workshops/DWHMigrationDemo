-- Converted: explicit order detail view
-- Reason: listing columns explicitly stabilizes the view contract
CREATE OR ALTER VIEW sales.v_orders AS
SELECT order_id, customer_id, order_date, total_amount
FROM sales.orders;
