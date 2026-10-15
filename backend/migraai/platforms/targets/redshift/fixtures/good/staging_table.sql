-- Converted: temporary order staging table
-- Reason: a temporary table is session-scoped and uses an explicit distribution choice
CREATE TEMP TABLE stage_orders (
    order_id BIGINT,
    customer_id BIGINT,
    order_date DATE
)
DISTSTYLE EVEN;
