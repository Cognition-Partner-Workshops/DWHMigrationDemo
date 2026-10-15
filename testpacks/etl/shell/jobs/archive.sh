#!/bin/sh
# nightly archive job

load_config() {
    . /etc/etl/archive.conf
}

start_archive() {
    log_msg "archive start"
}

archive_orders() {
    bteq <<'EOSQL'
.LOGON tdw1/etluser,etlpass
INSERT INTO arc.orders
SELECT order_id, cust_id, order_ts, amount
FROM mart.orders
WHERE order_ts < DATE '2023-01-01';
.LOGOFF
EOSQL
}

archive_customers() {
    nzsql -host nz1 -u etluser -pw secret -d dwh -c "INSERT INTO arc.customers SELECT * FROM mart.customers"
}

compress_dumps() {
    gzip /data/dumps/*.dat
    log_msg "compressed"
}

move_to_cold() {
    mv /data/dumps/*.gz /cold/incoming/
    log_msg "moved"
}

verify_counts() {
    snowsql -a dwh_wh -q "SELECT COUNT(*) FROM arc.orders"
}

finish_archive() {
    cleanup_tmp
    notify_done "/data/dumps/marker.flag"
}
