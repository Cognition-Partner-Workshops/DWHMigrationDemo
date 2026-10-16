-- Converted: order refresh procedures
-- Reason: procedures must use the supported body form and avoid forbidden commands
CREATE PROCEDURE sales.bad_refresh()
AS BEGIN
    SELECT 1;
END;

CREATE PROCEDURE sales.bad_maintenance()
AS $$
BEGIN
    VACUUM sales.orders;
END;
$$ LANGUAGE plpgsql;
