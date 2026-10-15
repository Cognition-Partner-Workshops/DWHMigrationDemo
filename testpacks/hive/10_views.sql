CREATE VIEW v_customer_orders AS
SELECT o.id AS order_id, c.id AS customer_id
FROM raw_orders o
JOIN raw_customers c ON o.id = c.id;

CREATE VIEW v_normalized_customer AS
SELECT normalize(c.name) AS normalized_name
FROM raw_customers c;

CREATE VIEW v_external_legacy AS
SELECT legacy_id FROM legacy_archive;

CREATE TABLE agg_daily_sales AS
SELECT COUNT(*) AS order_count FROM raw_orders;
