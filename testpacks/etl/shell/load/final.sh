#!/bin/sh
# load stage into marts

load_orders() {
    bteq <<'EOSQL'
.LOGON tdw1/etluser,etlpass
INSERT INTO mart.orders
SELECT order_id, cust_id, order_ts, amount
FROM stg.orders;
.LOGOFF
EOSQL
}

load_customers() {
    nzsql -host nz1 -u etluser -pw secret -d dwh -c "INSERT INTO mart.customers SELECT * FROM stg.customers"
}

rebuild_dims() {
    beeline -u jdbc:hive2://hn1:10000 -e "INSERT OVERWRITE TABLE mart.dim_dates SELECT * FROM stg.dim_dates"
}

load_facts() {
    hive -f load/facts.hql
}

export_marts() {
    bcp mart.orders out /data/orders.dat -S dbsrv -U etluser -P secretpw -c
}

refresh_cache() {
    snowsql -a dwh_wh -q "SELECT cust_id, total FROM mart.cust_summary"
}
