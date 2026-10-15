-- Converted: fake credential literal and unsafe grant examples
-- Reason: use Secret Manager and narrow table-level IAM roles
SET token = 'example-not-a-real-secret';
GRANT `roles/viewer` ON TABLE `analytics-prod.sales.orders` TO "group:analytics-readers@example.test";
GRANT `roles/bigquery.dataOwner` ON SCHEMA `analytics-prod.sales` TO "group:analytics-admins@example.test";
GRANT `roles/bigquery.dataViewer` ON TABLE `analytics-prod.sales.orders` TO "allAuthenticatedUsers";
