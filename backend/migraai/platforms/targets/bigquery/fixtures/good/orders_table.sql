-- Converted: clustered table uses an unenforced primary key declaration
-- Reason: BigQuery records key metadata without enforcing the constraint
CREATE TABLE `analytics-prod.sales.orders` (
  order_id INT64 NOT NULL,
  customer_id STRING,
  order_date DATE,
  amount NUMERIC,
  PRIMARY KEY (order_id) NOT ENFORCED
)
PARTITION BY order_date
CLUSTER BY customer_id, order_date;
