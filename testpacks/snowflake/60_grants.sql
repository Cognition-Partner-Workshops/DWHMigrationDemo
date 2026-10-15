GRANT SELECT ON TABLE analytics.curated.v_customer_orders TO ROLE role_analyst;
GRANT APPLY MASKING POLICY ON TABLE analytics.raw.customers TO ROLE role_analyst;
GRANT ROLE role_loader TO ROLE role_engineer;
GRANT USAGE ON DATABASE analytics TO SHARE share_partner;
