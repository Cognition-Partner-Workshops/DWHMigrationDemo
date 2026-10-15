CREATE TABLE analytics.raw.customers (
  id NUMBER IDENTITY(1,1),
  email STRING WITH MASKING POLICY analytics.security.mask_email,
  region STRING,
  created_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
  profile VARIANT
);
CREATE TABLE analytics.raw.orders (
  id NUMBER IDENTITY(1,1),
  customer_id NUMBER,
  amount NUMBER(12,2) DEFAULT 0,
  status STRING DEFAULT 'pending',
  payload VARIANT
);
CREATE TABLE analytics.raw.order_lines (
  order_id NUMBER,
  product_id NUMBER,
  quantity NUMBER DEFAULT 1,
  unit_price NUMBER(12,2)
);
CREATE TABLE analytics.raw.products (
  id NUMBER,
  name STRING,
  category STRING,
  attributes VARIANT,
  active BOOLEAN DEFAULT TRUE
);
CREATE TABLE analytics.raw.payments (
  id NUMBER,
  order_id NUMBER,
  amount NUMBER(12,2),
  currency STRING DEFAULT 'USD',
  paid_at TIMESTAMP_NTZ
);
CREATE TABLE analytics.raw.returns (
  id NUMBER,
  order_id NUMBER,
  reason STRING,
  returned_at TIMESTAMP_NTZ
);
CREATE TRANSIENT TABLE analytics.raw.events (
  id NUMBER IDENTITY(1,1),
  event_type STRING DEFAULT 'unknown',
  payload VARIANT,
  created_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
) CLUSTER BY (created_at);
CREATE TABLE analytics.raw.inventory (
  product_id NUMBER,
  warehouse_id NUMBER,
  available NUMBER DEFAULT 0,
  updated_at TIMESTAMP_NTZ
);
CREATE TABLE analytics.raw.currencies (
  code STRING,
  rate NUMBER(12,6),
  effective_at DATE
);
CREATE TABLE analytics.raw.shipping (
  order_id NUMBER,
  carrier STRING,
  tracking_number STRING,
  shipped_at TIMESTAMP_NTZ
);
CREATE TABLE analytics.raw.addresses (
  customer_id NUMBER,
  address_type STRING,
  city STRING,
  region STRING,
  postal_code STRING
);
CREATE TABLE analytics.raw.suppliers (
  id NUMBER,
  name STRING,
  country STRING,
  active BOOLEAN
);
CREATE TABLE analytics.raw.regions (
  id NUMBER,
  name STRING,
  country_code STRING
);
CREATE TABLE analytics.raw.discounts (
  id NUMBER,
  product_id NUMBER,
  percent NUMBER(5,2),
  valid_from DATE,
  valid_to DATE
);
CREATE ALERT broken_alert;
CREATE TABLE analytics.raw.recovery_survivor (id NUMBER, recovered_at TIMESTAMP_NTZ);
