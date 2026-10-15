CREATE INDEX sales.ix_customer_status (status) ON sales.customer;
CREATE UNIQUE INDEX sales.ix_account_customer (customer_id) ON sales.account;
CREATE HASH INDEX sales.hix_account (account_id) ON sales.account BY (customer_id);
CREATE HASH INDEX sales.hix_card (card_id) ON sales.card;
CREATE JOIN INDEX sales.jix_order AS
SELECT h.order_id, l.product_id FROM sales.order_header h
JOIN sales.order_line l ON h.order_id = l.order_id;
CREATE SYNONYM sales.customer_alias FOR sales.customer;
