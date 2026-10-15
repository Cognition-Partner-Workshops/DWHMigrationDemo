-- Converted: read access is limited to a named analytics role
-- Reason: the role receives one privilege on one table
GRANT SELECT ON TABLE ANALYTICS.SALES.ORDERS TO ROLE SALES_READER;
