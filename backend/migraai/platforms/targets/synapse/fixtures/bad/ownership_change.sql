-- Converted: object owner update
-- Reason: converted code should grant narrow permissions rather than change object ownership
ALTER AUTHORIZATION ON OBJECT::sales.orders TO [schema_owner];
