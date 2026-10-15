CREATE PROCEDURE p_validate_order()
BEGIN
    CALL p_audit_order();
END;

CREATE PROCEDURE p_audit_order()
BEGIN
    INSERT INTO audit_events (id) VALUES (1);
END;

CREATE FUNCTION fn_order_total(amount INT) RETURNS INT
BEGIN
    RETURN amount + 1;
END;

CREATE FUNCTION fn_adjust_total(amount INT) RETURNS INT
BEGIN
    RETURN fn_order_total(amount);
END;
