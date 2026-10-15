# datastage

DataStage `.dsx` parser (design §13): DSX text (`BEGIN DSJOB`/`DSRECORD`/
`DSSUBRECORD`, `=+=+=+=` blocks) and DSExport XML via the informatica router —
both map onto a shared Job/Record model. Expression language in `expr.py`
(`lnk.col`, `If…Then…Else`, `#param#`, `:` concat).
