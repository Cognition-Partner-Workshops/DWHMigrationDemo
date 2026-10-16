-- Converted: unsafe credential and grant examples are isolated
-- Reason: converted SQL must not broaden access or embed credentials
SET api_key = 'example-not-a-real-secret';
GRANT ALL ON TABLE main.sales.orders TO sales_reader;
GRANT MANAGE ON CATALOG main TO account_admin;
GRANT SELECT ON TABLE main.sales.orders TO users;
