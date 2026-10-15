-- Converted: distributed orders table
-- Reason: the customer distribution key and order-date sort key suit the query pattern
CREATE TABLE sales.orders (
    order_id BIGINT NOT NULL,
    customer_id BIGINT NOT NULL,
    order_date DATE NOT NULL,
    total_amount DECIMAL(12, 2) NOT NULL
)
DISTSTYLE KEY
DISTKEY (customer_id)
SORTKEY (order_date);
