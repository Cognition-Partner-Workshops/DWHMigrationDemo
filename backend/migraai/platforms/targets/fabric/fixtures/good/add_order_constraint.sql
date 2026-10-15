-- Converted: non-enforced order identifier constraint
-- Reason: Fabric constraints are added separately and declared NOT ENFORCED
ALTER TABLE sales.orders
ADD CONSTRAINT UQ_orders_order_id UNIQUE NONCLUSTERED (order_id) NOT ENFORCED;
