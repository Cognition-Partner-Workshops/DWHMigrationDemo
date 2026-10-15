-- Converted: reporting identity and prediction call
-- Reason: identities are provisioned by GRANT and model prediction is handled outside this warehouse surface
CREATE USER migration_reader;
SELECT PREDICT(MODEL = 'sample_model', INPUT_DATA = sales.orders);
