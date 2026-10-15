CREATE ROLE reader;
CREATE ROLE operator;
CREATE ROLE auditor;

CREATE USER analyst FROM sales AS PERM = 0, PASSWORD = 'demo-secret', DEFAULT DATABASE = sales;
CREATE USER loader FROM sales AS PERM = 0, PASSWORD = 'demo-secret', DEFAULT DATABASE = sales;
GRANT reader TO analyst;
GRANT operator TO loader;

GRANT SELECT ON TABLE sales.customer_accounts TO reader;
GRANT SELECT ON TABLE sales.account_payments TO reader;
GRANT SELECT, INSERT ON TABLE sales.payment TO operator;
GRANT SELECT ON TABLE sales.audit_log TO auditor;
GRANT EXECUTE PROCEDURE ON sales.add_payment TO operator;
