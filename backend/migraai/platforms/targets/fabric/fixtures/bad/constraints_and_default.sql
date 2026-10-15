-- Converted: inline order constraints
-- Reason: add constraints separately as NOT ENFORCED and provide default values in the load
CREATE TABLE sales.bad_constraints (
    order_id BIGINT PRIMARY KEY,
    amount DECIMAL(12, 2) DEFAULT 0
);
