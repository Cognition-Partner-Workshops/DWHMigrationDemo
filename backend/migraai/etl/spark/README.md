# spark ETL parser

Handles `.py` (pyspark, delegated from `python.parse`), `.scala` and `.json`.

## Objects
- `.py`: same `module`/`function` objects as the python parser (extra spark
  call handlers).
- `.scala`: `scala_object` qualified `<package>.<Name>` for `object`/`class`/
  `trait`; `function` `<package>.<Name>.<def>` for `def`s. Node names
  `spark.scala_object` / `spark.scala_function`.
- `.json`: `spark_submit` (qualified by rel_path) for Spark/Livy submit
  configs; `etl_runs` refs to `file` (path, split=False) and `className`
  (dotted).

## References / EmbeddedSql (pyspark)
`spark.sql(lit)` / `sqlContext.sql` → EmbeddedSql (hint None);
`spark.table("t")`, `spark.read.table("t")` → etl_reads;
`.write…saveAsTable/.insertInto/.writeTo` → etl_writes;
`.format("jdbc").option("dbtable","t")…load()` and `.jdbc(url,"t")` →
reads/writes; `.option("query",lit)` → EmbeddedSql; `DeltaTable.forName` →
etl_reads; `.parquet/.csv/.json/.orc/.load/.save(path)` → etl_reads/etl_writes
(split=False).

## Scala
Tokenizer + call-pattern matching on token sequences (no full grammar).
`spark.sql("…")` → EmbeddedSql; table/path refs as above.

## Control-M delegation
A `.json` whose top-level values are dicts with `Type` starting
Folder/SimpleFolder/Calendar is a Control-M Automation API export → lazy
`import migraai.etl.controlm.parse`; if absent → warning
`spark.controlm_unavailable` and no objects.

## Known gaps
- `.scala` is not routed by the core (core change request — see report).
- Scala generics/method bodies are best-effort; `def`s without `=`/`{`
  (signatures only) are skipped.
