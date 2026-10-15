-- Converted: unfiltered source mutations retained for review
-- Reason: destructive writes must include a deliberate row predicate
UPDATE sales.orders AS target SET amount = 0;
DELETE FROM sales.orders;
