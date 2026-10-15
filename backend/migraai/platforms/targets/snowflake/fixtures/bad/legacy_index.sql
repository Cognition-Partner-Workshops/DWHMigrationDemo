-- Converted: source secondary index statement
-- Reason: Snowflake clustering and search optimization use different constructs
CREATE UNIQUE CLUSTERED INDEX ix_orders_customer
ON ANALYTICS.SALES.ORDERS (CUSTOMER_ID);
