#!/bin/sh
# daily extract

prepare_stage() {
    cleanup_tmp
    log_msg "stage ready"
}

extract_orders() {
    bteq <<'EOSQL'
.LOGON tdw1/etluser,etlpass
SELECT order_id, cust_id, order_ts, amount
FROM src.orders
WHERE order_ts >= DATE '2024-01-01';
.LOGOFF
EOSQL
}

extract_customers() {
    bteq <<'EOSQL'
.LOGON tdw1/etluser,etlpass
SELECT cust_id, cust_name, region
FROM src.customers;
.LOGOFF
EOSQL
}

extract_products() {
    sqlplus etluser@dwh <<'EOSQL'
SELECT prod_id, prod_name, price FROM src.products;
EOSQL
}

stage_files() {
    nzload -host nz1 -u etluser -pw secret -db dwh -t stg.stage_files -df /data/files.dat
}

verify_counts() {
    db2 "SELECT COUNT(*) FROM src.audit_log"
}
