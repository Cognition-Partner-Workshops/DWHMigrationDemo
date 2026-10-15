-- Converted: managed-identity order load
-- Reason: COPY INTO uses managed identity without embedding storage credentials
COPY INTO sales.orders
FROM 'https://example.invalid/data/orders.csv'
WITH (
    FILE_TYPE = 'CSV',
    CREDENTIAL = (IDENTITY = 'Managed Identity')
);
