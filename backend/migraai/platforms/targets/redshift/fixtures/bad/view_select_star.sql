-- Converted: order detail view
-- Reason: the projection should be made explicit before deployment
CREATE VIEW sales.v_bad_orders AS
SELECT * FROM sales.orders;
