GRANT SELECT ON TABLE fact_orders TO analyst;
GRANT INSERT, UPDATE ON TABLE dim_customers TO app_role;
GRANT SELECT ON VIEW v_customer_summary TO reporting_role;
