-- Converted: order identifier sequence and validation
-- Reason: generate identifiers through a supported design and validate rows outside constraints
CREATE SEQUENCE sales.order_sequence AS BIGINT START WITH 1 INCREMENT BY 1;
CREATE TABLE sales.bad_amounts (
    order_id BIGINT,
    amount DECIMAL(12, 2) CONSTRAINT ck_positive CHECK (amount > 0)
);
