-- Converted: source query constructs retained for validator coverage
-- Reason: each construct needs a Snowflake-compatible replacement
SELECT TOP 5 order_id FROM ANALYTICS.SALES.ORDERS;
SELECT order_id FROM ANALYTICS.SALES.ORDERS WITH (NOLOCK);
SELECT [sales].[orders].order_id FROM [sales].[orders];
SELECT GETUTCDATE();
SELECT * FROM ANALYTICS.SALES.ORDERS;
