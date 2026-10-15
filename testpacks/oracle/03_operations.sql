CREATE TABLE oe.audit_events (event_id NUMBER PRIMARY KEY, actor_id NUMBER);
CREATE TABLE oe.audit_users (user_id NUMBER PRIMARY KEY, user_name VARCHAR2(100 CHAR));
CREATE TABLE oe.returns (return_id NUMBER PRIMARY KEY, line_id NUMBER REFERENCES oe.order_lines(line_id));
CREATE TABLE oe.shipments (shipment_id NUMBER PRIMARY KEY, order_id NUMBER REFERENCES oe.orders(order_id));
CREATE TABLE oe.shipment_lines (shipment_line_id NUMBER PRIMARY KEY, shipment_id NUMBER REFERENCES oe.shipments(shipment_id), line_id NUMBER REFERENCES oe.order_lines(line_id));
CREATE TABLE oe.currency_rates (rate_id NUMBER PRIMARY KEY, currency_code CHAR(3));
CREATE TABLE oe.fraud_signals (signal_id NUMBER PRIMARY KEY, customer_id NUMBER REFERENCES oe.customers(customer_id));
CREATE TABLE oe.customer_notes (note_id NUMBER PRIMARY KEY, customer_id NUMBER REFERENCES oe.customers(customer_id));
CREATE TABLE oe.loyalty_accounts (account_id NUMBER PRIMARY KEY, customer_id NUMBER REFERENCES oe.customers(customer_id));
CREATE TABLE oe.store_locations (store_id NUMBER PRIMARY KEY, region_id NUMBER REFERENCES oe.regions(region_id));

CREATE TABLE oe.stage_orders (order_id NUMBER, customer_id NUMBER, status VARCHAR2(20 CHAR)) ON COMMIT PRESERVE ROWS;
CREATE GLOBAL TEMPORARY TABLE oe.stage_lines (line_id NUMBER, order_id NUMBER) ON COMMIT DELETE ROWS;
CREATE SYNONYM oe.order_history FOR oe.orders;
CREATE PUBLIC SYNONYM customer_directory FOR oe.customers;
CREATE BITMAP INDEX oe.orders_status_bix ON oe.orders (status);
CREATE DATABASE LINK reporting_link CONNECT TO report_user IDENTIFIED BY 'synthetic-secret' USING 'REPORTING';
