import { useEffect, useState } from "react";

import type { ConvertEstimate } from "./api";
import { acuCapWarning } from "./acuCap";

export function ConvertConsentDialog({
  estimate,
  open,
  onCancel,
  onConfirm,
}: {
  estimate: ConvertEstimate | null;
  open: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    if (open) {
      setChecked(false);
    }
  }, [open]);

  if (!open || !estimate) {
    return null;
  }

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={onCancel}>
      <section
        aria-labelledby="convert-consent-title"
        aria-modal="true"
        className="convert-consent-dialog"
        role="dialog"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="dialog-header">
          <div>
            <span className="eyebrow">CONVERSION CONSENT</span>
            <h2 id="convert-consent-title">Send code to Devin?</h2>
          </div>
          <button aria-label="Close consent dialog" className="icon-button" onClick={onCancel}>
            ×
          </button>
        </header>
        <p>
          Converting sends the source code of {estimate.objects} selected objects to Devin at{" "}
          {estimate.devin_host}, in {estimate.voter_sessions} sessions (up to {estimate.max_sessions}{" "}
          if low-confidence objects need escalation).{" "}
          {estimate.acu_cap === null ? (
            <>These sessions have no ACU limit.</>
          ) : (
            <>
              Each session is limited to {estimate.acu_limit_per_session} ACU (up to{" "}
              {estimate.max_acus} ACUs for the run, cap {estimate.acu_cap}).
            </>
          )}{" "}
          Each one
          stops after {estimate.session_timeout_minutes} minutes, or when you cancel. Nothing else
          from your files, your settings or your Devin key is sent. Your consent is recorded in the
          audit log.
        </p>
        {acuCapWarning(estimate) && (
          <p className="acu-cap-warning">{acuCapWarning(estimate)}</p>
        )}
        <label className="consent-check">
          <input
            checked={checked}
            onChange={(event) => setChecked(event.target.checked)}
            type="checkbox"
          />
          <span>I understand that this code leaves this computer</span>
        </label>
        <div className="dialog-actions">
          <button className="button secondary" onClick={onCancel} type="button">
            Cancel
          </button>
          <button
            className="button primary"
            disabled={!checked}
            onClick={onConfirm}
            type="button"
          >
            Send to Devin
          </button>
        </div>
      </section>
    </div>
  );
}
