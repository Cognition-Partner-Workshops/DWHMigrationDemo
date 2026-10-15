-- Converted: temporary connection settings
-- Reason: replace sample secrets with managed identity authentication
DECLARE @password varchar(64);
SET @password = 'NotARealPassw0rd';
CREATE EXTERNAL DATA SOURCE order_storage
WITH (
    LOCATION = 'https://example.invalid/data',
    CREDENTIAL = (IDENTITY = 'Shared Access Signature', SECRET = 'NotARealSecret')
);
