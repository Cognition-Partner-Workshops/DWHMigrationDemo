-- Converted: procedure missing an explicit execution context
-- Reason: choose the least-privilege execution mode deliberately
CREATE OR REPLACE PROCEDURE ANALYTICS.SALES.REBUILD_ORDERS()
RETURNS VARCHAR
LANGUAGE SQL
AS
$$
BEGIN
  RETURN 'rebuilt';
END;
$$;
