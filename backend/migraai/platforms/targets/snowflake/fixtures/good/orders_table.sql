-- Converted: native Snowflake table with semi-structured payload data
-- Reason: VARIANT supports the source JSON document without a source-only type
CREATE OR REPLACE TABLE ANALYTICS.SALES.ORDERS (
  ORDER_ID NUMBER(18, 0),
  CUSTOMER_ID VARCHAR,
  ORDER_DATE DATE,
  DETAILS VARIANT
);
