-- Converted: order aggregation is exposed as a BigQuery SQL procedure
-- Reason: the scheduled job can invoke a reusable dataset-scoped routine
CREATE OR REPLACE PROCEDURE `analytics-prod.sales.refresh_orders`()
BEGIN
  INSERT INTO `analytics-prod.sales.daily_order_totals` (order_date, order_count)
  SELECT order_date, COUNT(*)
  FROM `analytics-prod.sales.orders`
  GROUP BY order_date;
END;
