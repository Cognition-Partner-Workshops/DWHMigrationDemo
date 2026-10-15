-- Converted: fake credential and unsafe privilege examples
-- Reason: use hashed or externally managed credentials and narrow grants
CREATE USER demo_user IDENTIFIED BY 'example-not-a-real-password';
CREATE TABLE sales.external_orders ENGINE = S3('https://example.test/orders.csv', 'AKIAEXAMPLEEXAMPLE00', 'example-not-a-real-secret', 'CSV');
GRANT ALL ON *.* TO analytics_reader;
GRANT SELECT ON sales.orders TO analytics_reader WITH GRANT OPTION;
GRANT SELECT ON sales.orders TO public;
