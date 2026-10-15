-- Converted: distributed order fact table
-- Reason: customer-key distribution and clustered columnstore fit the analytical workload
CREATE TABLE sales.orders (
    order_id BIGINT NOT NULL,
    customer_id BIGINT NOT NULL,
    order_date DATE NOT NULL,
    total_amount DECIMAL(12, 2) NOT NULL
)
WITH (
    DISTRIBUTION = HASH(customer_id),
    CLUSTERED COLUMNSTORE INDEX
);
