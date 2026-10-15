-- Converted: read-only reporting access
-- Reason: the named role receives only the SELECT privilege it needs
GRANT SELECT ON TABLE sales.orders TO ROLE report_reader;
