CREATE TABLE oe.orders (order_id NUMBER PRIMARY KEY, customer_id NUMBER REFERENCES oe.customers(customer_id), status VARCHAR2(20 CHAR) DEFAULT 'NEW');
CREATE TABLE oe.order_lines (line_id NUMBER PRIMARY KEY, order_id NUMBER REFERENCES oe.orders(order_id), product_id NUMBER REFERENCES oe.products(product_id));
CREATE TABLE oe.invoices (invoice_id NUMBER PRIMARY KEY, order_id NUMBER REFERENCES oe.orders(order_id));
CREATE TABLE oe.invoice_lines (line_id NUMBER PRIMARY KEY, invoice_id NUMBER REFERENCES oe.invoices(invoice_id), product_id NUMBER REFERENCES oe.products(product_id));
CREATE TABLE oe.payments (payment_id NUMBER PRIMARY KEY, invoice_id NUMBER REFERENCES oe.invoices(invoice_id));
CREATE TABLE oe.addresses (address_id NUMBER PRIMARY KEY, city VARCHAR2(80 CHAR));
CREATE TABLE oe.customer_addresses (customer_id NUMBER REFERENCES oe.customers(customer_id), address_id NUMBER REFERENCES oe.addresses(address_id));
CREATE TABLE oe.inventory (inventory_id NUMBER PRIMARY KEY, product_id NUMBER REFERENCES oe.products(product_id), warehouse_id NUMBER REFERENCES oe.warehouses(warehouse_id));
CREATE TABLE oe.price_history (price_id NUMBER PRIMARY KEY, product_id NUMBER REFERENCES oe.products(product_id));
CREATE TABLE oe.sales_targets (target_id NUMBER PRIMARY KEY, region_id NUMBER REFERENCES oe.regions(region_id));

CREATE VIEW oe.v_order_totals AS SELECT order_id, COUNT(*) AS line_count FROM oe.order_lines GROUP BY order_id;
CREATE MATERIALIZED VIEW oe.mv_customer_orders BUILD IMMEDIATE REFRESH FAST ON COMMIT AS SELECT customer_id, COUNT(*) AS order_count FROM oe.orders GROUP BY customer_id;
