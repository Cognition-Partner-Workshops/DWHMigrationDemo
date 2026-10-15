#!/bin/sh
# top-level orchestrator

SCRIPT_DIR=$(dirname $0)
. $SCRIPT_DIR/../lib/common.sh

main() {
    prepare_extract
    run_extract
    run_load
    finish_up
}

prepare_extract() {
    wait_for_lock
    sh $SCRIPT_DIR/../extract/daily.sh
}

run_extract() {
    extract_orders
    extract_customers
    extract_products
    stage_files
    verify_counts
}

run_load() {
    source $SCRIPT_DIR/../load/final.sh
    load_orders
    load_customers
    rebuild_dims
    load_facts
    export_marts
    refresh_cache
}

finish_up() {
    spark-submit --class com.acme.etl.Reconcile --master yarn jobs/reconcile.jar
    python3 jobs/notify.py
    report_status
    cleanup_tmp
}

main "$@"
