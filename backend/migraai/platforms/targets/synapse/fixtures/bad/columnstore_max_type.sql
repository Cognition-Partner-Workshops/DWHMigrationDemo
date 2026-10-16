-- Converted: long order notes table
-- Reason: use bounded column types or a HEAP when storing MAX types
CREATE TABLE sales.order_notes (
    note_text NVARCHAR(MAX)
);
