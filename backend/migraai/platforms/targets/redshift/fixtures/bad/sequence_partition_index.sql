-- Converted: partitioned event history
-- Reason: unsupported sequence, partitioning, and index features need redesign
CREATE SEQUENCE sales.event_sequence;
CREATE TABLE sales.partitioned_events (
    event_id BIGINT,
    event_date DATE
)
PARTITION BY RANGE (event_date);
CREATE INDEX event_date_idx ON sales.partitioned_events (event_date);
