-- Converted: generated order identifier
-- Reason: Fabric requires BIGINT and does not allow custom identity seed or increment values
CREATE TABLE sales.bad_identity (
    order_id INT IDENTITY(1, 1)
);
