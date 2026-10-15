-- Converted: source query constructs retained for validator coverage
-- Reason: each construct needs a ClickHouse-compatible rewrite
SELECT * FROM sales.orders WITH (NOLOCK);
SELECT GETDATE();
