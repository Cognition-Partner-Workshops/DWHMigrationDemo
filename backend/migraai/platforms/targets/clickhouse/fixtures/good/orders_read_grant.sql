-- Converted: read access is limited to the analytics reader role
-- Reason: the role receives only SELECT on the orders database
GRANT SELECT ON sales.orders TO analytics_reader;
