-- Converted: legacy secondary index statement
-- Reason: the table layout should use Delta clustering instead
CREATE INDEX ix_orders_customer ON main.sales.orders (customer_id);
