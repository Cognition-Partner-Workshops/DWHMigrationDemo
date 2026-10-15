-- Converted: explicit order summary view
-- Reason: named columns keep the view contract stable
CREATE VIEW sales.v_order_totals AS
SELECT customer_id, SUM(total_amount) AS total_amount
FROM sales.orders
GROUP BY customer_id;
