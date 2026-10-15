#!/bin/sh
# shared helpers

log_msg() {
    echo "$(date) $1"
}

fail() {
    log_msg "ERROR $1"
    exit 1
}

retry() {
    attempts=$1
    shift
    while [ "$attempts" -gt 0 ]; do
        "$@" && return 0
        attempts=$((attempts - 1))
        sleep 5
    done
    return 1
}

check_file() {
    if [ -f "$1" ]; then
        return 0
    fi
    fail "missing $1"
}

archive_file() {
    check_file "$1"
    mv "$1" "$2"
    log_msg "archived $1"
}

notify_done() {
    log_msg "done $1"
    archive_file "$1" "$1.done"
}

wait_for_lock() {
    while [ -f /tmp/etl.lock ]; do
        sleep 10
    done
    log_msg "lock released"
}

cleanup_tmp() {
    rm -f /tmp/etl_*.tmp
    log_msg "cleaned"
}

run_sql_file() {
    check_file "$1"
    sqlplus etluser@dwh @"$1"
    notify_done "$1"
}

report_status() {
    sqlplus -s etluser@dwh <<'EOSQL'
SELECT job_name, status FROM ctl.job_log WHERE run_date = CURRENT_DATE;
EOSQL
}
