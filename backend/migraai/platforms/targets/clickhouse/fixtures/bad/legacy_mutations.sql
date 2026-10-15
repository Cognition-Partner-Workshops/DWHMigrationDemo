-- Converted: heavyweight mutation and unfiltered deletion forms retained
-- Reason: use lightweight or replacement-row operations with a predicate
CREATE INDEX ix_orders_customer ON sales.orders (customer_id);
ALTER TABLE sales.orders UPDATE amount = 0 WHERE order_id = 1;
ALTER TABLE sales.orders DELETE WHERE order_id = 1;
DELETE FROM sales.orders;
