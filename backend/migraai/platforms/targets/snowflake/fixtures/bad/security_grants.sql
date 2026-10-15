-- Converted: unsafe credential and grant examples are isolated
-- Reason: credentials and broad grants require a secure replacement
SET api_key = 'example-not-a-real-secret';
GRANT ALL ON TABLE ANALYTICS.SALES.ORDERS TO ROLE SALES_READER;
GRANT ROLE ACCOUNTADMIN TO ROLE SALES_READER;
GRANT SELECT ON TABLE ANALYTICS.SALES.ORDERS TO ROLE PUBLIC;
