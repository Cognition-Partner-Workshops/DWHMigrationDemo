-- Converted: staging order CTAS
-- Reason: identity values must be generated in a supported table definition instead of CTAS
CREATE TABLE stage.orders_ctas
WITH (DISTRIBUTION = ROUND_ROBIN, HEAP)
AS
SELECT IDENTITY(BIGINT, 1, 1) AS order_id, customer_id
FROM sales.orders;
