-- Converted: reporting role access
-- Reason: the named role receives only the required SELECT permission
GRANT SELECT ON OBJECT::sales.orders TO [report_reader];
