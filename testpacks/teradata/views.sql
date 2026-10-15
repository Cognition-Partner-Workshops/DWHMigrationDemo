CREATE VIEW sales.customer_accounts AS
SELECT c.customer_id, c.customer_name, a.account_id, a.balance
FROM sales.customer c JOIN sales.account a ON c.customer_id = a.customer_id;

CREATE VIEW sales.account_payments AS
SELECT a.account_id, p.payment_id, p.amount
FROM sales.account a JOIN sales.payment p ON a.account_id = p.account_id;

CREATE VIEW sales.customer_loans AS
SELECT c.customer_id, l.loan_id, l.principal
FROM sales.customer c JOIN sales.loan l ON c.customer_id = l.customer_id;

CREATE VIEW sales.order_detail AS
SELECT h.order_id, l.product_id, p.product_name
FROM sales.order_header h
JOIN sales.order_line l ON h.order_id = l.order_id
JOIN sales.product p ON l.product_id = p.product_id;

CREATE VIEW sales.card_activity AS
SELECT c.card_id, t.txn_id, t.amount
FROM sales.card c JOIN sales.card_txn t ON c.card_id = t.card_id;

CREATE VIEW sales.transfer_activity AS
SELECT transfer_id, from_account, to_account FROM sales.transfer;

CREATE VIEW sales.customer_risk AS
SELECT c.customer_id, r.score, s.segment_code
FROM sales.customer c
JOIN sales.risk_score r ON c.customer_id = r.customer_id
JOIN sales.customer_segment s ON c.customer_id = s.customer_id;

CREATE VIEW sales.daily_account_balances AS
SELECT account_id, amount FROM sales.daily_balance;

CREATE VIEW sales.customer_external_rate AS
SELECT c.customer_id, e.rate FROM sales.customer c
JOIN sales.external_rate e ON e.rate_date = DATE '2025-01-01';

CREATE VIEW sales.unuploaded_reference AS
SELECT id FROM sales.customer_archive;
