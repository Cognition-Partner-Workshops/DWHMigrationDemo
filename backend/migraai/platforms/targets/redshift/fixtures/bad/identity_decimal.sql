-- Converted: generated order identifier
-- Reason: Redshift identity columns require an integer type
CREATE TABLE sales.bad_identity (
    order_id DECIMAL(12, 0) IDENTITY(1, 1)
);
