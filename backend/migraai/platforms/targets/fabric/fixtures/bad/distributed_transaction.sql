-- Converted: cross-database transaction
-- Reason: replace the unsupported distributed transaction with pipeline orchestration
BEGIN DISTRIBUTED TRANSACTION;
UPDATE sales.orders SET total_amount = 0;
COMMIT TRANSACTION;
