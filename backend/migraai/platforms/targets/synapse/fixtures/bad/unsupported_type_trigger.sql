-- Converted: spatial order metadata and trigger
-- Reason: remove unsupported spatial types and move trigger work into the load process
CREATE TABLE sales.bad_metadata (location GEOGRAPHY);
CREATE TRIGGER sales.bad_trigger
ON sales.orders
AFTER INSERT
AS
SELECT 1;
