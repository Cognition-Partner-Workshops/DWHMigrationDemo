CREATE TABLE dbo.MonthlySales (id INT PRIMARY KEY);
CREATE TABLE dbo.LegacySales (id INT PRIMARY KEY);
CREATE TABLE dbo.ImportBatch (id INT PRIMARY KEY);
CREATE TABLE dbo.LoadError (id INT PRIMARY KEY);
CREATE TABLE dbo.Reconciliation (id INT PRIMARY KEY);
CREATE TABLE dbo.ExternalMap (id INT PRIMARY KEY);
CREATE TABLE dbo.RiskScore (id INT PRIMARY KEY);
CREATE TABLE dbo.ArchiveMarker (id INT PRIMARY KEY);
CREATE TABLE dbo.DataQuality (id INT PRIMARY KEY);
CREATE TABLE dbo.EventQueue (id INT PRIMARY KEY);

CREATE EXTERNAL DATA SOURCE lake WITH (TYPE = HADOOP, LOCATION = 'https://example.test/lake');
CREATE EXTERNAL FILE FORMAT csv_format WITH (FORMAT_TYPE = DELIMITEDTEXT, FORMAT_OPTIONS (FIELD_TERMINATOR = ','));
CREATE DATABASE SCOPED CREDENTIAL lake_credential WITH (IDENTITY = 'Managed Identity', SECRET = 'synthetic-secret');
CREATE EXTERNAL TABLE dbo.external_sales (id INT) WITH (LOCATION = '/sales/', DATA_SOURCE = lake, FILE_FORMAT = csv_format);
CREATE MATERIALIZED VIEW dbo.mv_sales WITH (DISTRIBUTION = HASH(id)) AS SELECT id FROM dbo.Sales;
CREATE VIEW dbo.vw_remote_sales AS SELECT id FROM ExternalDb.dbo.RemoteSales;
CREATE PROCEDURE dbo.usp_rollup AS BEGIN SELECT id FROM dbo.vw_remote_sales; EXEC dbo.usp_load; END
GO
CREATE PROCEDURE dbo.usp_load AS BEGIN SELECT id FROM dbo.Sales; END
GO
CREATE WORKLOAD GROUP ingest WITH (MIN_PERCENTAGE_RESOURCE = 10, CAP_PERCENTAGE_RESOURCE = 50);
CREATE WORKLOAD CLASSIFIER etl_user WITH (WORKLOAD_GROUP = 'ingest', MEMBERNAME = 'etl_user', IMPORTANCE = HIGH);
CREATE STATISTICS st_sales ON dbo.Sales (id);
COPY INTO dbo.Sales FROM 'https://example.test/sales.csv' WITH (FILE_TYPE = 'CSV', CREDENTIAL = (IDENTITY = 'SAS', SECRET = 'synthetic-secret'));
RENAME OBJECT::dbo.LegacySales TO dbo.LegacySalesOld;

CREATE TABLE dbo.Broken (id INT, CONSTRAINT broken_fk FOREIGN KEY (id) REFERENCES );
CREATE XML SCHEMA COLLECTION dbo.unsupported AS '<schema/>';
CREATE TABLE dbo.recovery_survivor (id INT);
