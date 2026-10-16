-- Converted: Fabric orders table
-- Reason: BIGINT IDENTITY is the supported generated-key type
CREATE TABLE sales.orders (
    order_id BIGINT IDENTITY NOT NULL,
    customer_id BIGINT NOT NULL,
    order_date DATE NOT NULL,
    total_amount DECIMAL(12, 2) NOT NULL
);
