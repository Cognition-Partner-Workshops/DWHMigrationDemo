-- Converted: PL/pgSQL refresh procedure
-- Reason: the procedure body uses the supported dollar-quoted PL/pgSQL form
CREATE OR REPLACE PROCEDURE sales.refresh_orders()
AS $$
BEGIN
    INSERT INTO sales.order_audit (event_name)
    VALUES ('refresh');
END;
$$ LANGUAGE plpgsql;
