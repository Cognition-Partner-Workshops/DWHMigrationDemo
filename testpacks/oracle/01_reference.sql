CREATE TABLE oe.customers (customer_id NUMBER PRIMARY KEY, customer_name VARCHAR2(100 CHAR));
CREATE TABLE oe.products (product_id NUMBER PRIMARY KEY, product_name VARCHAR2(100 CHAR));
CREATE TABLE oe.regions (region_id NUMBER PRIMARY KEY, region_name VARCHAR2(80 CHAR));
CREATE TABLE oe.employees (employee_id NUMBER PRIMARY KEY, manager_id NUMBER);
CREATE TABLE oe.suppliers (supplier_id NUMBER PRIMARY KEY, supplier_name VARCHAR2(100 CHAR));
CREATE TABLE oe.warehouses (warehouse_id NUMBER PRIMARY KEY, region_id NUMBER REFERENCES oe.regions(region_id));
CREATE TABLE oe.categories (category_id NUMBER PRIMARY KEY, category_name VARCHAR2(80 CHAR));
CREATE TABLE oe.brands (brand_id NUMBER PRIMARY KEY, brand_name VARCHAR2(80 CHAR));
CREATE TABLE oe.promotions (promotion_id NUMBER PRIMARY KEY, promotion_name VARCHAR2(100 CHAR));
CREATE TABLE oe.tax_rates (tax_rate_id NUMBER PRIMARY KEY, rate NUMBER(8,4));

CREATE SEQUENCE oe.customer_seq START WITH 100 INCREMENT BY 1 NOCACHE;
CREATE UNIQUE INDEX oe.customers_name_uix ON oe.customers (customer_name);
