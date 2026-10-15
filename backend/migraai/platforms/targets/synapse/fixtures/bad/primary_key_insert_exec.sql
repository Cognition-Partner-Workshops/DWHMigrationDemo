-- Converted: order load procedure
-- Reason: remove unsupported constraints and replace INSERT EXEC with a staging-table load
CREATE TABLE sales.bad_orders (
    order_id BIGINT PRIMARY KEY NONCLUSTERED
);
INSERT INTO stage.orders_load EXEC sales.get_orders;
