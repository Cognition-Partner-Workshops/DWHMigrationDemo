-- Converted: table read access is granted to a named analytics group
-- Reason: the group receives a predefined data-viewer role on one table
GRANT `roles/bigquery.dataViewer`
ON TABLE `analytics-prod.sales.orders`
TO "group:analytics-readers@example.test";
