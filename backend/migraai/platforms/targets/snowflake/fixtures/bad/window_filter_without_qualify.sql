-- Converted: source window filter retained without target QUALIFY
-- Reason: QUALIFY expresses the row-number filter directly
SELECT order_id
FROM (
  SELECT order_id, ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY order_date DESC) AS rn
  FROM ANALYTICS.SALES.ORDERS
) AS ranked
WHERE rn = 1;
