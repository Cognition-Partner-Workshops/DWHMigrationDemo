-- Converted: legacy error-reporting procedure
-- Reason: remove unsupported execution context and error-line handling
CREATE PROCEDURE sales.bad_refresh
WITH EXECUTE AS OWNER
AS
SELECT ERROR_LINE();
