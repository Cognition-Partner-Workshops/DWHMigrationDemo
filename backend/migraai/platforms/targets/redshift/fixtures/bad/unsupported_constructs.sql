-- Converted: order metadata and maintenance objects
-- Reason: these source features need replacement before conversion is complete
CREATE TABLE sales.bad_metadata (payload JSONB);
SELECT GENERATE_SERIES(1, 3);
CREATE TRIGGER sales.bad_trigger
AFTER INSERT ON sales.orders
FOR EACH ROW
EXECUTE FUNCTION sales.refresh_order_row();
