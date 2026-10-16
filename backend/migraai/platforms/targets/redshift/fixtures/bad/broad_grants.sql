-- Converted: initial reporting grants
-- Reason: broad and delegable permissions should be reduced to named least-privilege grants
GRANT ALL ON TABLE sales.orders TO ROLE report_reader;
GRANT SELECT ON TABLE sales.orders TO PUBLIC;
GRANT UPDATE ON TABLE sales.orders TO ROLE report_reader WITH GRANT OPTION;
