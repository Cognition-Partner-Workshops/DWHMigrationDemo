CREATE TABLE oe.monthly_sales (month_id NUMBER PRIMARY KEY);
CREATE TABLE oe.legacy_sales (legacy_id NUMBER PRIMARY KEY);
CREATE TABLE oe.import_batches (batch_id NUMBER PRIMARY KEY);
CREATE TABLE oe.load_errors (error_id NUMBER PRIMARY KEY);
CREATE TABLE oe.reconciliations (reconciliation_id NUMBER PRIMARY KEY);
CREATE TABLE oe.external_mappings (mapping_id NUMBER PRIMARY KEY);
CREATE TABLE oe.risk_scores (score_id NUMBER PRIMARY KEY);
CREATE TABLE oe.archive_markers (marker_id NUMBER PRIMARY KEY);
CREATE TABLE oe.data_quality (check_id NUMBER PRIMARY KEY);
CREATE TABLE oe.event_queue (event_id NUMBER PRIMARY KEY);

CREATE OR REPLACE PACKAGE oe.order_api AS
  PROCEDURE submit_order(p_order_id IN NUMBER);
END order_api;
/
CREATE OR REPLACE PACKAGE BODY oe.order_api AS
  PROCEDURE submit_order(p_order_id IN NUMBER) IS
  BEGIN
    INSERT INTO oe.orders(order_id, status) VALUES (p_order_id, 'NEW');
    oe.audit_pkg.write_event(p_order_id);
  END submit_order;
END order_api;
/
CREATE OR REPLACE PROCEDURE oe.close_order(p_order_id IN NUMBER) IS
BEGIN
  UPDATE oe.orders SET status = 'CLOSED' WHERE order_id = p_order_id;
END close_order;
/
CREATE OR REPLACE TRIGGER oe.orders_audit
AFTER INSERT OR UPDATE OF status ON oe.orders FOR EACH ROW
BEGIN
  oe.audit_pkg.write_event(:NEW.order_id);
END orders_audit;
/
CREATE OR REPLACE FUNCTION oe.order_total(p_order_id IN NUMBER) RETURN NUMBER IS
  v_total NUMBER := 0;
BEGIN
  SELECT COUNT(*) INTO v_total FROM oe.order_lines WHERE order_id = p_order_id;
  RETURN v_total;
END order_total;
/
CREATE USER report_user IDENTIFIED BY 'synthetic-secret';
CREATE ROLE oe_reader;
GRANT SELECT ON oe.orders TO oe_reader;
CREATE VIEW oe.v_reporting_orders AS SELECT order_id FROM oe.orders@reporting_link;

CREATE TABLE oe.broken_statement (id NUMBER, CONSTRAINT broken_fk FOREIGN KEY (id) REFERENCES );
CREATE XML SCHEMA COLLECTION oe.unsupported;
CREATE TABLE oe.recovery_survivor (id NUMBER);
