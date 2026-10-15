package com.acme.etl

import org.apache.spark.sql.SparkSession

/**
 * Daily reconciliation: join staged payments against the payments fact table
 * and persist the delta snapshot for the finance close.
 */
object Reconcile {
  def main(args: Array[String]): Unit = {
    val spark = SparkSession.builder()
      .appName("reconcile-payments")
      .getOrCreate()

    val staged = spark.read.table("stg.payments_stage")
    val fact = spark.read.format("jdbc")
      .option("url", "jdbc:sqlserver://dw01;databaseName=dw")
      .option("dbtable", "mart.payment_fact")
      .load()

    val delta = staged.join(fact, Seq("payment_id"), "left_anti")
    delta.write.saveAsTable("mart.payment_recon_delta")
    spark.stop()
  }
}
