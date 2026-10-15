-- Converted: read access is granted to a named reporting role
-- Reason: the role receives only the permission needed on one table
GRANT SELECT ON TABLE main.sales.orders TO sales_reader;
