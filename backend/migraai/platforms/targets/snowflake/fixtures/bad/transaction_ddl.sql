-- Converted: permanent DDL inside an explicit transaction
-- Reason: Snowflake DDL commits independently of the transaction
BEGIN TRANSACTION;
CREATE TABLE ANALYTICS.SALES.TEMP_ORDERS (ORDER_ID NUMBER);
COMMIT;
