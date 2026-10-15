CREATE TABLE dbo.MonthlySales (id INT PRIMARY KEY);
CREATE TABLE dbo.LegacyOrder (id INT PRIMARY KEY);
CREATE TABLE dbo.ImportBatch (id INT PRIMARY KEY);
CREATE TABLE dbo.LoadError (id INT PRIMARY KEY);
CREATE TABLE dbo.Reconciliation (id INT PRIMARY KEY);
CREATE TABLE dbo.ExternalMap (id INT PRIMARY KEY);
CREATE TABLE dbo.RiskScore (id INT PRIMARY KEY);
CREATE TABLE dbo.ArchiveMarker (id INT PRIMARY KEY);
CREATE TABLE dbo.DataQuality (id INT PRIMARY KEY);
CREATE TABLE dbo.EventQueue (id INT PRIMARY KEY);

CREATE VIEW dbo.vw_order_totals AS
SELECT o.id, SUM(l.id) AS total FROM dbo.Orders o JOIN dbo.OrderLine l ON l.order_id = o.id GROUP BY o.id;
CREATE VIEW dbo.vw_remote_orders AS
SELECT id FROM LinkedServer.SalesDb.dbo.RemoteOrders;
CREATE PROCEDURE dbo.usp_calc_tax @order_id INT AS
BEGIN
    SELECT rate FROM dbo.TaxRate;
END
GO
CREATE PROCEDURE dbo.usp_refresh_sales @order_id INT AS
BEGIN
    SELECT id FROM dbo.vw_order_totals;
    EXEC dbo.usp_calc_tax @order_id = @order_id;
END
GO
CREATE FUNCTION dbo.fn_sales_tax(@amount DECIMAL(10,2)) RETURNS DECIMAL(10,2) AS
BEGIN
    RETURN @amount * 0.1;
END
GO
CREATE TRIGGER dbo.trg_orders ON dbo.Orders AFTER INSERT AS
BEGIN
    SELECT id FROM inserted;
END
GO
CREATE SEQUENCE dbo.order_seq START WITH 1 INCREMENT BY 1;
CREATE SYNONYM dbo.syn_customer FOR dbo.Customer;
GRANT SELECT ON OBJECT::dbo.Orders TO analyst;

CREATE TABLE dbo.Broken (id INT, CONSTRAINT broken_fk FOREIGN KEY (id) REFERENCES );
CREATE XML SCHEMA COLLECTION dbo.unsupported AS '<schema/>';
CREATE TABLE dbo.recovery_survivor (id INT);
