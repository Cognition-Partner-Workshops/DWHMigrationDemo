CREATE PROCEDURE sales.add_payment(IN p_account BIGINT, IN p_amount DECIMAL(12, 2))
LANGUAGE SQL DYNAMIC RESULT SETS 1 SQL SECURITY OWNER
BEGIN
  INSERT INTO sales.payment (payment_id, account_id, amount) VALUES (1, p_account, p_amount);
  INSERT INTO sales.audit_log (event_id, event_type) VALUES (1, 'PAYMENT');
END;

CREATE PROCEDURE sales.update_balance(IN p_account BIGINT, IN p_amount DECIMAL(12, 2))
LANGUAGE SQL
BEGIN
  UPDATE sales.account SET balance = balance + p_amount WHERE account_id = p_account;
END;

CREATE PROCEDURE sales.post_transfer(IN p_from BIGINT, IN p_to BIGINT, IN p_amount DECIMAL(12, 2))
LANGUAGE SQL
BEGIN
  INSERT INTO sales.transfer (transfer_id, from_account, to_account) VALUES (1, p_from, p_to);
  INSERT INTO sales.audit_log (event_id, event_type) VALUES (2, 'TRANSFER');
END;

CREATE PROCEDURE sales.capture_snapshot()
LANGUAGE SQL
BEGIN
  INSERT INTO sales.account_snapshot (snapshot_date, account_id)
  SELECT CURRENT_DATE, account_id FROM sales.account;
END;

CREATE PROCEDURE sales.repair_payment()
LANGUAGE SQL
BEGIN
  SELECT FROM ;
  INSERT INTO sales.audit_log (event_id, event_type) VALUES (3, 'REPAIR');
END;

CREATE MACRO sales.refresh_daily(p_day DATE DEFAULT CURRENT_DATE) AS (
  INSERT INTO sales.daily_balance (balance_date, account_id, amount)
  SELECT p_day, account_id, balance FROM sales.account;
);
