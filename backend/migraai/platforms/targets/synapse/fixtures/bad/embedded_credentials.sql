-- Converted: temporary connection settings
-- Reason: use managed identity instead of sample passwords and storage secrets
DECLARE @password varchar(64);
SET @password = 'NotARealPassw0rd';
COPY INTO sales.orders
FROM 'https://example.invalid/data/orders.csv'
WITH (CREDENTIAL = (IDENTITY = 'Shared Access Signature', SECRET = 'NotARealSecret'));
