-- Converted: legacy reporting settings
-- Reason: remove unsupported session settings and XML serialization
SET ROWCOUNT 10;
SELECT order_id FROM sales.orders FOR XML PATH('order');
