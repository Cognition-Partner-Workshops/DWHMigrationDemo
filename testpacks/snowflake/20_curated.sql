CREATE SECURE VIEW analytics.curated.v_customer_orders AS
SELECT c.id, c.email, o.id AS order_id, o.amount
FROM analytics.raw.customers c
JOIN analytics.raw.orders o ON o.customer_id = c.id;

CREATE VIEW analytics.curated.v_monthly_revenue AS
SELECT o.customer_id, SUM(o.amount) AS revenue
FROM analytics.raw.orders o
JOIN analytics.raw.order_lines l ON l.order_id = o.id
GROUP BY o.customer_id;

CREATE VIEW analytics.curated.v_inventory_risk AS
SELECT p.id, p.name, i.available
FROM analytics.raw.products p
JOIN analytics.raw.inventory i ON i.product_id = p.id;

CREATE VIEW analytics.curated.v_event_items AS
SELECT e.id, f.value:sku::STRING AS sku, f.value:quantity::NUMBER AS quantity
FROM analytics.raw.events e,
LATERAL FLATTEN(input => e.payload:items) f
QUALIFY ROW_NUMBER() OVER (PARTITION BY e.id ORDER BY f.index) = 1;

CREATE VIEW analytics.curated.v_external_legacy AS
SELECT id FROM analytics.raw.legacy_archive;

CREATE MATERIALIZED VIEW analytics.curated.mv_monthly_revenue AS
SELECT customer_id, SUM(amount) AS revenue
FROM analytics.raw.orders
GROUP BY customer_id;

CREATE TABLE analytics.curated.agg_daily_sales AS
SELECT customer_id, SUM(amount) AS total FROM analytics.raw.orders GROUP BY customer_id;

CREATE TABLE analytics.curated.agg_return_rates AS
SELECT order_id, COUNT(*) AS return_count FROM analytics.raw.returns GROUP BY order_id;

CREATE SEQUENCE analytics.curated.seq_order_id START WITH 1 INCREMENT BY 1 ORDER;
CREATE SEQUENCE analytics.curated.seq_batch_id START WITH 100 INCREMENT BY 10 NOORDER;
