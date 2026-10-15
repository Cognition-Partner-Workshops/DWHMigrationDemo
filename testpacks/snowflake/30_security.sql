CREATE ROLE role_engineer;
CREATE ROLE role_analyst;
CREATE ROLE role_loader;
CREATE USER user_service;
CREATE SHARE share_partner;
CREATE MASKING POLICY analytics.security.mask_email AS (v STRING)
  RETURNS STRING -> IFF(CURRENT_ROLE() = 'ROLE_ANALYST', v, '***');
CREATE ROW ACCESS POLICY analytics.security.row_region AS (r STRING)
  RETURNS BOOLEAN -> EXISTS (
    SELECT 1 FROM analytics.raw.regions allowed WHERE allowed.name = r
  );
CREATE TAG analytics.security.sensitivity ALLOWED_VALUES = ('public', 'restricted');
CREATE WAREHOUSE analytics.wh_etl WAREHOUSE_SIZE = 'SMALL' AUTO_SUSPEND = 60;
