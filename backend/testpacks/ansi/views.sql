CREATE VIEW v_customer_orders AS
SELECT o.id, c.id AS customer_id
FROM fact_orders AS o
JOIN dim_customers AS c ON o.id = c.id;

CREATE VIEW v_sales_by_product AS
SELECT o.id, p.id AS product_id
FROM fact_orders o
JOIN dim_products p ON o.id = p.id;

CREATE VIEW v_external AS
SELECT legacy.id FROM legacy_archive AS legacy;
