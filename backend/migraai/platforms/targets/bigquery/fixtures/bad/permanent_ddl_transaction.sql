-- Converted: permanent DDL occurs within a transaction
-- Reason: move permanent DDL outside BigQuery multi-statement transactions
BEGIN TRANSACTION;
CREATE TABLE sales.transaction_orders (order_id INT64);
COMMIT TRANSACTION;
