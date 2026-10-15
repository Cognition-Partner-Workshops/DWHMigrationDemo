import { useEffect, useState } from "react";

import {
  cancelKBRefresh,
  getKBStatus,
  rollbackKB,
  startKBRefresh,
  type KBStatusResponse,
} from "./api";

const REFRESH_PAGE_CAP = 3000;

function formatDate(value: string | null): string {
  if (!value) {
    return "Not available";
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export function KnowledgeBaseSettings({ active }: { active: boolean }) {
  const [status, setStatus] = useState<KBStatusResponse | null>(null);
  const [error, setError] = useState("");
  const [starting, setStarting] = useState(false);
  const [rollingBack, setRollingBack] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (!active) {
      return;
    }
    let mounted = true;
    let timer: number | undefined;

    async function load() {
      try {
        const next = await getKBStatus();
        if (!mounted) {
          return;
        }
        setStatus(next);
        setError("");
        if (next.running?.running) {
          timer = window.setTimeout(load, 2000);
        }
      } catch (loadError) {
        if (mounted) {
          setError(loadError instanceof Error ? loadError.message : "Could not load KB status.");
        }
      }
    }

    void load();
    return () => {
      mounted = false;
      if (timer !== undefined) {
        window.clearTimeout(timer);
      }
    };
  }, [active, reloadKey]);

  async function refresh() {
    setStarting(true);
    setError("");
    try {
      await startKBRefresh();
      setReloadKey((current) => current + 1);
    } catch (refreshError) {
      setError(refreshError instanceof Error ? refreshError.message : "Could not start refresh.");
    } finally {
      setStarting(false);
    }
  }

  async function cancelRefresh() {
    setError("");
    try {
      await cancelKBRefresh();
      setReloadKey((current) => current + 1);
    } catch (cancelError) {
      setError(cancelError instanceof Error ? cancelError.message : "Could not cancel refresh.");
    }
  }

  async function rollback(version: number) {
    if (!window.confirm(`Roll back the knowledge base to version ${version}?`)) {
      return;
    }
    setRollingBack(true);
    setError("");
    try {
      await rollbackKB();
      setReloadKey((current) => current + 1);
    } catch (rollbackError) {
      setError(rollbackError instanceof Error ? rollbackError.message : "Could not roll back.");
    } finally {
      setRollingBack(false);
    }
  }

  const isRunning = Boolean(status?.running?.running);
  const currentVersion = status?.current_version;
  const olderVersions =
    !status || currentVersion === null || currentVersion === undefined
      ? []
      : status.rollback_versions.filter((version) => version < currentVersion);
  const rollbackTarget = olderVersions.length > 0 ? Math.max(...olderVersions) : null;
  const progress = status?.running;

  return (
    <section aria-labelledby="kb-settings-heading" className="kb-settings-section">
      <div className="settings-section-heading">
        <div>
          <h3 id="kb-settings-heading">Knowledge base</h3>
          <p>Seeded vendor knowledge and retained versions</p>
        </div>
        <div className="kb-actions">
          <button
            className="button primary compact-button"
            disabled={!status || isRunning || starting}
            onClick={() => void refresh()}
            type="button"
          >
            {starting ? "Starting…" : "Refresh"}
          </button>
          {isRunning && (
            <button
              className="button secondary compact-button"
              onClick={() => void cancelRefresh()}
              type="button"
            >
              Cancel
            </button>
          )}
        </div>
      </div>
      {error && <p className="kb-error" role="alert">{error}</p>}
      {!status ? (
        <p className="kb-loading">Loading knowledge-base status…</p>
      ) : (
        <>
          <div className="kb-status-grid">
            <div><span>Active version</span><strong>{status.current_version ?? "—"}</strong></div>
            <div><span>Built</span><strong>{formatDate(status.created_at)}</strong></div>
            <div><span>Documents</span><strong>{status.documents.toLocaleString()}</strong></div>
            <div><span>Chunks</span><strong>{status.chunks.toLocaleString()}</strong></div>
            <div className={status.seed_error ? "seed-error" : ""}>
              <span>Seed errors</span>
              <strong>{status.seed_error ?? "None"}</strong>
            </div>
          </div>
          {progress && (
            <div className="kb-progress">
              <div className="kb-progress-copy">
                <strong>{progress.phase.replaceAll("_", " ")}</strong>
                <span>{progress.pages_fetched.toLocaleString()} / {REFRESH_PAGE_CAP.toLocaleString()} pages</span>
              </div>
              <progress
                aria-label="Knowledge-base refresh progress"
                max={REFRESH_PAGE_CAP}
                value={progress.pages_fetched}
              />
            </div>
          )}
          <div className="kb-version-section">
            <strong>Versions</strong>
            <ul className="kb-version-list">
              {status.current_version !== null && status.current_version !== undefined && (
                <li>
                  <span>v{status.current_version} <em>Active</em></span>
                  <small>{status.documents.toLocaleString()} documents · {status.chunks.toLocaleString()} chunks</small>
                </li>
              )}
              {status.rollback_versions.map((version) => (
                <li key={version}>
                  <span>v{version} <em>Retained</em></span>
                  {version === rollbackTarget && (
                    <button
                      className="text-button"
                      disabled={rollingBack || isRunning}
                      onClick={() => void rollback(version)}
                      type="button"
                    >
                      Roll back
                    </button>
                  )}
                </li>
              ))}
              {status.current_version === null && status.rollback_versions.length === 0 && (
                <li><span>No retained versions</span></li>
              )}
            </ul>
          </div>
        </>
      )}
    </section>
  );
}
