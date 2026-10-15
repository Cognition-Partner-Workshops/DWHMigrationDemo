-- Converted: MergeTree table definition is missing its sorting key
-- Reason: the table needs an ORDER BY key or ORDER BY tuple()
CREATE TABLE sales.unsorted_orders (order_id UInt64)
ENGINE = MergeTree;
