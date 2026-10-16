-- Converted: daily totals use ClickHouse aggregate functions
-- Reason: grouping and projection are explicit for the analytical workload
SELECT order_date, count() AS order_count, sum(amount) AS total_amount
FROM sales.orders
GROUP BY order_date
ORDER BY order_date;
