-- Converted: source semi-structured type and staging name retained
-- Reason: use VARIANT and a permanent table name aligned to the target
CREATE OR REPLACE TABLE ANALYTICS.STG_ORDER_PAYLOAD (
  payload JSONB
);
