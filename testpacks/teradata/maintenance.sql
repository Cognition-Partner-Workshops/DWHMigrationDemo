CREATE TABLE sales.broken (id);
CREATE TABLE sales.recovered_after_error (id INTEGER);
CREATE AUTHORIZATION sales.unknown_auth;
COLLECT STATISTICS COLUMN (customer_id) ON sales.customer;
COLLECT STATISTICS INDEX (account_id) ON sales.account;
BT;
ET;
