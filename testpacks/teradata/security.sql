CREATE ROLE sales.reader;
CREATE ROLE sales.operator;
CREATE ROLE sales.auditor;

CREATE USER sales.analyst WITH PASSWORD 'demo-secret' IN GROUP sales.reader;
CREATE USER sales.loader WITH PASSWORD 'demo-secret' IN GROUP sales.operator;

GRANT SELECT ON TABLE sales.customer_accounts TO sales.reader;
GRANT SELECT ON TABLE sales.account_payments TO sales.reader;
GRANT SELECT, INSERT ON TABLE sales.payment TO sales.operator;
GRANT SELECT ON TABLE sales.audit_log TO sales.auditor;
GRANT EXECUTE PROCEDURE ON sales.add_payment TO sales.operator;
