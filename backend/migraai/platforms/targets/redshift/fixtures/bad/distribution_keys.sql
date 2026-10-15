-- Converted: customer and region distribution attempts
-- Reason: Redshift requires a single DISTKEY and DISTSTYLE KEY must specify it
CREATE TABLE sales.bad_multi_key (
    customer_id BIGINT,
    region_id INTEGER
)
DISTKEY (customer_id, region_id);

CREATE TABLE sales.bad_missing_key (
    customer_id BIGINT
)
DISTSTYLE KEY;
