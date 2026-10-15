-- Converted: Synapse order refresh procedure
-- Reason: bounded WHILE work and THROW provide explicit control flow and error reporting
CREATE PROCEDURE sales.refresh_orders
AS
BEGIN
    DECLARE @batch_id INT = 0;
    WHILE @batch_id < 2
    BEGIN
        SET @batch_id += 1;
        IF @batch_id = 2
            THROW 50001, 'Batch complete', 1;
    END;
END;
