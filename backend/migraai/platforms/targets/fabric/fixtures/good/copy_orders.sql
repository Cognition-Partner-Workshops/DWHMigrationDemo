-- Converted: managed-identity order load
-- Reason: COPY INTO uses an identity rather than an embedded storage secret
COPY INTO sales.orders
FROM 'https://example.invalid/data/orders.csv'
WITH (
    FILE_TYPE = 'CSV',
    CREDENTIAL = (IDENTITY = 'Managed Identity')
);
