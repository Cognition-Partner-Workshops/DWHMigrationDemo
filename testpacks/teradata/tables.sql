DATABASE sales;

CREATE SET TABLE sales.customer (
  customer_id INTEGER NOT NULL,
  customer_name VARCHAR(80) NOT NULL,
  status CHAR(1) DEFAULT 'A'
) PRIMARY INDEX (customer_id);

CREATE MULTISET TABLE sales.account (
  account_id BIGINT NOT NULL,
  customer_id INTEGER REFERENCES sales.customer(customer_id),
  balance DECIMAL(14, 2) DEFAULT 0
) PRIMARY INDEX (account_id);

CREATE SET TABLE sales.branch (branch_id INTEGER, branch_name VARCHAR(80)) PRIMARY INDEX (branch_id);
CREATE SET TABLE sales.employee (employee_id INTEGER, branch_id INTEGER) PRIMARY INDEX (employee_id);
CREATE SET TABLE sales.product (product_id INTEGER, product_name VARCHAR(80)) PRIMARY INDEX (product_id);
CREATE SET TABLE sales.order_header (order_id BIGINT, customer_id INTEGER) PRIMARY INDEX (order_id);
CREATE SET TABLE sales.order_line (order_id BIGINT, product_id INTEGER) PRIMARY INDEX (order_id, product_id);
CREATE SET TABLE sales.payment (payment_id BIGINT, account_id BIGINT, amount DECIMAL(12, 2)) PRIMARY INDEX (payment_id);
CREATE SET TABLE sales.loan (loan_id BIGINT, customer_id INTEGER, principal DECIMAL(14, 2)) PRIMARY INDEX (loan_id);
CREATE SET TABLE sales.loan_payment (loan_id BIGINT, payment_date DATE, amount DECIMAL(12, 2)) PRIMARY INDEX (loan_id);
CREATE SET TABLE sales.card (card_id BIGINT, account_id BIGINT) PRIMARY INDEX (card_id);
CREATE SET TABLE sales.card_txn (txn_id BIGINT, card_id BIGINT, amount DECIMAL(12, 2)) PRIMARY INDEX (txn_id);
CREATE SET TABLE sales.transfer (transfer_id BIGINT, from_account BIGINT, to_account BIGINT) PRIMARY INDEX (transfer_id);
CREATE SET TABLE sales.audit_log (event_id BIGINT, event_type VARCHAR(40)) PRIMARY INDEX (event_id);
CREATE SET TABLE sales.risk_score (customer_id INTEGER, score DECIMAL(5, 2)) PRIMARY INDEX (customer_id);
CREATE SET TABLE sales.account_snapshot (snapshot_date DATE, account_id BIGINT) PRIMARY INDEX (account_id);
CREATE SET TABLE sales.customer_segment (customer_id INTEGER, segment_code VARCHAR(12)) PRIMARY INDEX (customer_id);
CREATE SET TABLE sales.external_rate (rate_date DATE, rate DECIMAL(6, 4)) PRIMARY INDEX (rate_date);
CREATE SET TABLE sales.daily_balance (balance_date DATE, account_id BIGINT, amount DECIMAL(14, 2)) PRIMARY INDEX (account_id);
CREATE SET TABLE sales.recovered_table (id INTEGER);
