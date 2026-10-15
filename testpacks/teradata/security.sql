CREATE ROLE reader;
CREATE ROLE operator;
CREATE ROLE auditor;

CREATE USER analyst WITH PASSWORD 'demo-secret' IN GROUP reader;
CREATE USER loader WITH PASSWORD 'demo-secret' IN GROUP operator;

GRANT SELECT ON TABLE sales.customer_accounts TO reader;
GRANT SELECT ON TABLE sales.account_payments TO reader;
GRANT SELECT, INSERT ON TABLE sales.payment TO operator;
GRANT SELECT ON TABLE sales.audit_log TO auditor;
GRANT EXECUTE PROCEDURE ON sales.add_payment TO operator;
