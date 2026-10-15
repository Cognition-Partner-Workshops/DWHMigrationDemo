-- Converted: initial reporting grants
-- Reason: broad and delegable permissions should be reduced to named least-privilege grants
GRANT ALL ON OBJECT::sales.orders TO [report_reader];
GRANT SELECT ON OBJECT::sales.orders TO PUBLIC;
GRANT UPDATE ON OBJECT::sales.orders TO [analyst] WITH GRANT OPTION;
