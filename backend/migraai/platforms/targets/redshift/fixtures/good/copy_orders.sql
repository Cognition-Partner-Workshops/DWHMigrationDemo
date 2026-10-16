-- Converted: IAM-role based order load
-- Reason: the cluster's default IAM role supplies storage access without embedded credentials
COPY sales.orders
FROM 's3://example-bucket/orders/'
IAM_ROLE default
FORMAT AS CSV
IGNOREHEADER 1;
