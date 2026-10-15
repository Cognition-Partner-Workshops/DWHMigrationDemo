-- Converted: source transaction wrapper retained for review
-- Reason: ClickHouse multi-statement transaction support is experimental
BEGIN TRANSACTION;
INSERT INTO sales.orders (order_id) VALUES (1);
COMMIT;
