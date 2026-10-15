-- Converted: heap staging table
-- Reason: a heap with round-robin distribution supports high-throughput staging loads
CREATE TABLE stage.orders_load (
    order_id BIGINT NOT NULL,
    customer_id BIGINT NOT NULL,
    order_date DATE NOT NULL
)
WITH (
    DISTRIBUTION = ROUND_ROBIN,
    HEAP
);
