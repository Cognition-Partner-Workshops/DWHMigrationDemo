-- Converted: order metadata and trigger
-- Reason: replace unsupported data types and move trigger logic into a pipeline
CREATE TABLE sales.bad_metadata (description NVARCHAR(100));
CREATE TRIGGER sales.bad_trigger
ON sales.orders
AFTER INSERT
AS
SELECT 1;
