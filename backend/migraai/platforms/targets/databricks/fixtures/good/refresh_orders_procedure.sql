-- Converted: refresh logic is encapsulated in a SQL procedure
-- Reason: the operation can be invoked consistently by the orchestration job
CREATE PROCEDURE main.sales.refresh_orders()
LANGUAGE SQL
AS
BEGIN
  INSERT INTO main.sales.daily_order_totals (order_date, order_count)
  SELECT order_date, COUNT(*)
  FROM main.sales.orders
  GROUP BY order_date;
END;
