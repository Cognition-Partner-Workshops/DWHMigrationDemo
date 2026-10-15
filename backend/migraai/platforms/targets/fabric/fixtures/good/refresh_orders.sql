-- Converted: Fabric order refresh procedure
-- Reason: warehouse-native stored procedure code keeps the transformation close to its data
CREATE PROCEDURE sales.refresh_orders
AS
BEGIN
    INSERT INTO sales.order_audit (event_name)
    VALUES ('refresh');
END;
