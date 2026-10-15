-- Converted: legacy query patterns retained for validator coverage
-- Reason: each source construct needs a target-specific rewrite
SELECT TOP 10 * FROM main.sales.orders WITH (NOLOCK);
SELECT GETUTCDATE();
SELECT * FROM main.sales.orders;
