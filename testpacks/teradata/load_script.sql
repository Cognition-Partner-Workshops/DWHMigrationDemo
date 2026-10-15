.LOGON db/loader,do-not-retain,$tdwallet(secret)
.SET QUIET ON
.EXPORT FILE = /tmp/customer.csv
SEL customer_id, customer_name FROM sales.customer;
.EXPORT RESET
.LOGOFF
