-- Converted: generated order sequence and aggregate view
-- Reason: replace sequences and materialized views with supported alternatives
CREATE SEQUENCE sales.order_sequence AS BIGINT START WITH 1 INCREMENT BY 1;
CREATE MATERIALIZED VIEW sales.v_order_totals AS
SELECT customer_id, SUM(total_amount) AS total_amount
FROM sales.orders
GROUP BY customer_id;
