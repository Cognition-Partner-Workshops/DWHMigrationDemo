CREATE VIEW v_customer_orders AS
SELECT o.order_id, c.customer_id, o.order_total, c.region
FROM raw_orders o
JOIN raw_customers c ON o.customer_id = c.customer_id;

CREATE VIEW v_normalized_customer AS
SELECT normalize(c.first_name) AS normalized_name
FROM raw_customers c;

CREATE VIEW v_external_legacy AS
SELECT legacy_id FROM legacy_archive;

CREATE VIEW v_event_tags AS
SELECT e.event_id, tag
FROM raw_events e
LATERAL VIEW explode(e.tags) exploded AS tag;

CREATE TABLE agg_daily_sales AS
SELECT sale_day, region, COUNT(*) AS order_count, SUM(order_total) AS gross_sales
FROM raw_orders
GROUP BY sale_day, region;
