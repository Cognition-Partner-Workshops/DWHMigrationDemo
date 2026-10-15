import { useEffect, useRef, useState } from "react";
import type { DragEvent, FormEvent } from "react";

import {
  apiRequest,
  confirmSession,
  exchangeLaunchToken,
  getCurrentSession,
  getPlan,
  type HealthResponse,
  type Platform,
  type PlatformResponse,
  type SessionView,
  type SettingsResponse,
  type SettingsUpdate,
} from "./api";
import { KnowledgeBaseSettings } from "./KnowledgeBaseSettings";
import { PlanningPanel } from "./PlanningPanel";
import { ResourcesPanel } from "./ResourcesPanel";
import logo from "./assets/logo.png";

const EMPTY_SETTINGS: SettingsResponse = {
  devin_pat: { configured: false, masked: "" },
  devin_org_id: "",
  token_kind: "none",
  devin_api_base_url: "https://api.devin.ai",
  confidence_threshold: 0.85,
  max_concurrent_sessions: 4,
  acu_cap_per_run: null,
};

const MAX_FILE_BYTES = 100 * 1024 * 1024;
// Enable when a later phase produces real converted output.
const downloadAvailable = false;
const ALLOWED_EXTENSIONS = new Set([
  ".sql",
  ".ddl",
  ".txt",
  ".zip",
  ".xml",
  ".dtsx",
  ".dsx",
  ".py",
  ".sh",
  ".scala",
  ".json",
  ".yaml",
  ".yml",
  ".infa",
]);

type FileRow = {
  id: string;
  file: File;
  status: "Pending" | "Accepted" | "Error" | "Preview complete";
  message: string;
};

function formatSize(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function extensionOf(fileName: string): string {
  const index = fileName.lastIndexOf(".");
  return index < 0 ? "" : fileName.slice(index).toLowerCase();
}

function SectionTitle({
  number,
  title,
  headingId,
  description,
}: {
  number: string;
  title: string;
  headingId: string;
  description?: string;
}) {
  return (
    <div className="section-title">
      <span className="section-number">{number}</span>
      <h2 id={headingId}>{title}</h2>
      {description && <p>{description}</p>}
    </div>
  );
}

export function SettingsDialog({
  open,
  settings,
  onClose,
  onSave,
}: {
  open: boolean;
  settings: SettingsResponse;
  onClose: () => void;
  onSave: (updates: SettingsUpdate) => Promise<void>;
}) {
  const [pat, setPat] = useState("");
  const [clearPat, setClearPat] = useState(false);
  const [orgId, setOrgId] = useState(settings.devin_org_id);
  const [apiBaseUrl, setApiBaseUrl] = useState(settings.devin_api_base_url);
  const [threshold, setThreshold] = useState(String(settings.confidence_threshold));
  const [concurrency, setConcurrency] = useState(String(settings.max_concurrent_sessions));
  const [acuCap, setAcuCap] = useState(
    settings.acu_cap_per_run === null ? "" : String(settings.acu_cap_per_run),
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (open) {
      setOrgId(settings.devin_org_id);
      setApiBaseUrl(settings.devin_api_base_url);
      setThreshold(String(settings.confidence_threshold));
      setConcurrency(String(settings.max_concurrent_sessions));
      setAcuCap(settings.acu_cap_per_run === null ? "" : String(settings.acu_cap_per_run));
      setPat("");
      setClearPat(false);
      setError("");
    }
  }, [open, settings]);

  if (!open) {
    return null;
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError("");
    const updates: SettingsUpdate = {
      devin_org_id: orgId,
      devin_api_base_url: apiBaseUrl,
      confidence_threshold: Number(threshold),
      max_concurrent_sessions: Number(concurrency),
      acu_cap_per_run: acuCap.trim() === "" ? null : Number(acuCap),
    };
    if (clearPat) {
      updates.devin_pat = "";
    } else if (pat) {
      updates.devin_pat = pat;
    }
    try {
      await onSave(updates);
      onClose();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Unable to save settings.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        aria-labelledby="settings-title"
        aria-modal="true"
        className="settings-dialog"
        role="dialog"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="dialog-header">
          <div>
            <span className="eyebrow">LOCAL CONFIGURATION</span>
            <h2 id="settings-title">Settings</h2>
          </div>
          <button aria-label="Close settings" className="icon-button" onClick={onClose}>
            ×
          </button>
        </header>
        <form onSubmit={submit}>
          <label className="form-field">
            <span>Devin personal access token</span>
            {settings.devin_pat.configured && (
              <small className="muted">Current token: {settings.devin_pat.masked}</small>
            )}
            <input
              autoComplete="new-password"
              name="devin_pat"
              onChange={(event) => setPat(event.target.value)}
              placeholder={
                settings.devin_pat.configured ? settings.devin_pat.masked : "Enter token"
              }
              type="password"
              value={pat}
            />
            {settings.devin_pat.configured && (
              <span className="clear-token">
                <input
                  checked={clearPat}
                  id="clear-token"
                  onChange={(event) => setClearPat(event.target.checked)}
                  type="checkbox"
                />
                <label htmlFor="clear-token">Clear saved token</label>
              </span>
            )}
          </label>
          <section aria-labelledby="devin-settings-heading" className="settings-section">
            <h3 id="devin-settings-heading">Devin</h3>
            <label className="form-field">
              <span>Organization ID</span>
              <small className="muted">Not needed for legacy <code>apk_</code> keys</small>
              <input onChange={(event) => setOrgId(event.target.value)} value={orgId} />
            </label>
            <label className="form-field">
              <span>API base URL</span>
              <input
                onChange={(event) => setApiBaseUrl(event.target.value)}
                type="url"
                value={apiBaseUrl}
              />
            </label>
            <div className="settings-grid">
              <label className="form-field">
                <span>Confidence threshold</span>
                <input
                  max="1"
                  min="0.5"
                  onChange={(event) => setThreshold(event.target.value)}
                  step="0.01"
                  type="number"
                  value={threshold}
                />
              </label>
              <label className="form-field">
                <span>Max concurrent sessions</span>
                <input
                  max="50"
                  min="1"
                  onChange={(event) => setConcurrency(event.target.value)}
                  type="number"
                  value={concurrency}
                />
              </label>
            </div>
            <label className="form-field">
              <span>ACU cap per run</span>
              <input
                min="0.01"
                onChange={(event) => setAcuCap(event.target.value)}
                placeholder="No cap"
                step="any"
                type="number"
                value={acuCap}
              />
            </label>
          </section>
          <KnowledgeBaseSettings active={open} />
          {error && <p className="inline-error" role="alert">{error}</p>}
          <div className="dialog-actions">
            <button className="button secondary" onClick={onClose} type="button">
              Cancel
            </button>
            <button className="button primary" disabled={saving} type="submit">
              {saving ? "Saving…" : "Save settings"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

export default function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [platforms, setPlatforms] = useState<PlatformResponse>({ sources: [], targets: [] });
  const [settings, setSettings] = useState<SettingsResponse>(EMPTY_SETTINGS);
  const [source, setSource] = useState("");
  const [target, setTarget] = useState("");
  const [confirmedPair, setConfirmedPair] = useState<{
    source: string;
    target: string;
  } | null>(null);
  const [sessionConfirmed, setSessionConfirmed] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [session, setSession] = useState<SessionView | null>(null);
  const [files, setFiles] = useState<FileRow[]>([]);
  const [hasUploaded, setHasUploaded] = useState(false);
  const [converted, setConverted] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const mainRef = useRef<HTMLElement | null>(null);
  const planningSectionRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    let active = true;
    async function bootstrap() {
      const launchToken = new URLSearchParams(window.location.search).get("launch");
      if (launchToken) {
        try {
          await exchangeLaunchToken(launchToken);
          window.history.replaceState(null, "", window.location.pathname);
        } catch (launchError) {
          if (active) {
            setError(
              launchError instanceof Error ? launchError.message : "Launch authentication failed.",
            );
          }
        }
      }

      const [nextHealth, nextPlatforms, nextSettings, currentSession] = await Promise.all([
        apiRequest<HealthResponse>("/api/health").catch(() => null),
        apiRequest<PlatformResponse>("/api/platforms").catch(() => null),
        apiRequest<SettingsResponse>("/api/settings").catch(() => null),
        getCurrentSession().catch(() => null),
      ]);
      if (!active) {
        return;
      }
      let restoredPlan = null;
      if (currentSession?.confirmed && currentSession.source && currentSession.target) {
        restoredPlan = await getPlan().catch(() => null);
      }
      if (!active) {
        return;
      }
      if (nextHealth) {
        setHealth(nextHealth);
      }
      if (nextPlatforms) {
        setPlatforms(nextPlatforms);
      } else {
        setError("Open MigraAI using the launch link printed by the local server.");
      }
      if (nextSettings) {
        setSettings(nextSettings);
      }
      if (currentSession?.confirmed && currentSession.source && currentSession.target) {
        setSource(currentSession.source);
        setTarget(currentSession.target);
        setConfirmedPair({ source: currentSession.source, target: currentSession.target });
        setSessionConfirmed(true);
        setSession({
          source: currentSession.source,
          target: currentSession.target,
          plan: restoredPlan,
        });
      }
    }
    void bootstrap();
    return () => {
      active = false;
    };
  }, []);

  const canConfirm = Boolean(
    source &&
      target &&
      source !== target &&
      !confirming &&
      (source !== confirmedPair?.source || target !== confirmedPair?.target),
  );
  const canUpload =
    sessionConfirmed &&
    !hasUploaded &&
    files.some(
      (row) =>
        row.status !== "Error" &&
        row.file.size <= MAX_FILE_BYTES &&
        ALLOWED_EXTENSIONS.has(extensionOf(row.file.name)),
    );

  async function confirmSystems() {
    if (!canConfirm) {
      return;
    }
    setConfirming(true);
    setError("");
    try {
      const response = await confirmSession(source, target);
      setSession({ source: response.source, target: response.target, plan: response.plan });
      setConfirmedPair({ source: response.source, target: response.target });
      setSessionConfirmed(true);
      requestAnimationFrame(() => {
        const main = mainRef.current;
        const planningSection = planningSectionRef.current;
        if (main && planningSection && typeof main.scrollTo === "function") {
          const prefersReducedMotion =
            window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
          main.scrollTo({
            top: planningSection.offsetTop - main.offsetTop,
            behavior: prefersReducedMotion ? "auto" : "smooth",
          });
        }
      });
      setFiles((current) =>
        current.map((row) =>
          row.status === "Pending"
            ? { ...row, message: "Ready to upload after confirmation." }
            : row,
        ),
      );
      setNotice("Source and target confirmed. Migration planning has started.");
    } catch (confirmError) {
      setError(confirmError instanceof Error ? confirmError.message : "Could not confirm systems.");
    } finally {
      setConfirming(false);
    }
  }

  function addFiles(fileList: FileList | File[]) {
    const incoming = Array.from(fileList);
    setFiles((current) => {
      const known = new Set(current.map((row) => `${row.file.name}:${row.file.size}`));
      const rows = incoming
        .filter((file) => !known.has(`${file.name}:${file.size}`))
        .map((file, index): FileRow => {
          const id = `${file.name}-${file.size}-${file.lastModified}-${current.length + index}`;
          if (file.size > MAX_FILE_BYTES) {
            return { id, file, status: "Error", message: "File exceeds the 100 MB limit." };
          }
          if (!ALLOWED_EXTENSIONS.has(extensionOf(file.name))) {
            return { id, file, status: "Error", message: "Unsupported file format." };
          }
          return {
            id,
            file,
            status: "Pending",
            message: sessionConfirmed ? "Ready to upload." : "Confirm source and target first.",
          };
        });
      return [...current, ...rows];
    });
    setHasUploaded(false);
    setConverted(false);
    setNotice("");
  }

  function uploadFiles() {
    const hasAcceptedFiles = files.some(
      (row) =>
        row.status !== "Error" &&
        row.file.size <= MAX_FILE_BYTES &&
        ALLOWED_EXTENSIONS.has(extensionOf(row.file.name)),
    );
    setFiles((current) =>
      current.map((row) =>
        row.status === "Error"
          ? row
          : { ...row, status: "Accepted", message: "Ready for conversion (local Phase 1 preview)." },
      ),
    );
    setHasUploaded(hasAcceptedFiles);
    setNotice(
      hasAcceptedFiles
        ? "Files are listed locally only; upload services are planned for a later phase."
        : "Choose a supported file no larger than 100 MB.",
    );
  }

  function previewConversion() {
    if (!hasUploaded) {
      return;
    }
    setFiles((current) =>
      current.map((row) =>
        row.status === "Accepted"
          ? {
              ...row,
              status: "Preview complete",
              message: "Preview complete — no conversion was run.",
            }
          : row,
      ),
    );
    setConverted(true);
    setNotice("This Phase 1 preview does not generate converted files.");
  }

  function removeFile(id: string) {
    setFiles((current) => current.filter((row) => row.id !== id));
    setHasUploaded(false);
    setConverted(false);
  }

  async function saveSettings(updates: SettingsUpdate) {
    const updated = await apiRequest<SettingsResponse>("/api/settings", {
      method: "PUT",
      body: updates,
    });
    setSettings(updated);
    setHealth((current) =>
      current ? { ...current, devin_configured: updated.devin_pat.configured } : current,
    );
  }

  function acceptDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    if (event.dataTransfer.files.length) {
      addFiles(event.dataTransfer.files);
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <img alt="" className="brand-logo" src={logo} />
          <div>
            <strong>MigraAI</strong>
            <span>Plan&nbsp; | &nbsp;Assess&nbsp; | &nbsp;Convert&nbsp; | &nbsp;Modernize</span>
          </div>
        </div>
        <p className="topbar-tagline">Your AI-powered data warehouse migration partner</p>
        <div className="topbar-actions">
          <div className="local-mode-tile">
            <svg aria-hidden="true" className="local-mode-icon" viewBox="0 0 24 24">
              <path
                d="M12 2.8 20 6v5.4c0 5.1-3.4 8.5-8 10.2-4.6-1.7-8-5.1-8-10.2V6l8-3.2Z"
                fill="none"
                stroke="currentColor"
                strokeLinejoin="round"
                strokeWidth="1.7"
              />
              <path
                d="m8.4 12.1 2.3 2.3 4.8-5"
                fill="none"
                stroke="currentColor"
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth="1.7"
              />
            </svg>
            <div className="local-mode-copy">
              <strong>Local Mode</strong>
              <span>
                {health?.devin_configured
                  ? "Conversions are sent to Devin cloud"
                  : "Your data stays on this machine"}
              </span>
            </div>
          </div>
          <button
            aria-label="Open settings"
            className="settings-trigger"
            onClick={() => setSettingsOpen(true)}
            type="button"
          >
            <span aria-hidden="true">⚙</span>
            Settings
          </button>
        </div>
      </header>

      <main className="dashboard" ref={mainRef}>
        <section aria-labelledby="system-heading" className="panel system-panel">
          <div className="panel-heading">
            <SectionTitle
              description="Select source and target data warehouse platforms"
              headingId="system-heading"
              number="1"
              title="System Selection"
            />
          </div>
          <div className="system-flow">
            <label className="platform-select">
              <span className="database-icon" aria-hidden="true">▤</span>
              <span className="platform-label">Source Database</span>
              <select
                aria-label="Source platform"
                onChange={(event) => setSource(event.target.value)}
                value={source}
              >
                <option value="">Choose a source…</option>
                {platforms.sources.map((platform: Platform) => (
                  <option key={platform.id} value={platform.id}>{platform.name}</option>
                ))}
              </select>
            </label>
            <span aria-hidden="true" className="flow-arrow">⇄</span>
            <button className="button primary confirm-button" disabled={!canConfirm} onClick={confirmSystems}>
              {confirming ? "Confirming…" : "Confirm"}
              <span aria-hidden="true">→</span>
            </button>
            <span aria-hidden="true" className="flow-arrow">⇄</span>
            <label className="platform-select">
              <span className="database-icon" aria-hidden="true">▤</span>
              <span className="platform-label">Target Database</span>
              <select
                aria-label="Target platform"
                onChange={(event) => setTarget(event.target.value)}
                value={target}
              >
                <option value="">Choose a target…</option>
                {platforms.targets.map((platform: Platform) => (
                  <option key={platform.id} value={platform.id}>{platform.name}</option>
                ))}
              </select>
            </label>
          </div>
          {error && <p className="banner error-banner" role="alert">{error}</p>}
        </section>

        <section
          aria-labelledby="planning-heading"
          className="panel planning-panel"
          ref={planningSectionRef}
        >
          <div className="panel-heading">
            <SectionTitle
              description="Comprehensive analysis and step-by-step migration plan"
              headingId="planning-heading"
              number="2"
              title="Migration Planning"
            />
            <span className="phase-chip">Phase 2</span>
          </div>
          <PlanningPanel
            confidenceThreshold={settings.confidence_threshold}
            session={sessionConfirmed ? session : null}
            sourceName={platforms.sources.find((item) => item.id === session?.source)?.name ?? ""}
            targetName={platforms.targets.find((item) => item.id === session?.target)?.name ?? ""}
          />
        </section>

        <section aria-labelledby="resources-heading" className="panel resources-panel">
          <div className="panel-heading">
            <SectionTitle
              description="Review and confirm available objects from source system"
              headingId="resources-heading"
              number="3"
              title="Resources Confirmation"
            />
          </div>
          <ResourcesPanel confirmed={sessionConfirmed} />
        </section>

        <section aria-labelledby="convert-heading" className="panel convert-panel">
          <div className="panel-heading convert-heading">
            <SectionTitle
              description="1. Browse or drop files · 2. Upload Files · 3. Convert Items"
              headingId="convert-heading"
              number="4"
              title="Convert Objects"
            />
            <div className="convert-actions">
              <button
                className="button upload-button"
                disabled={!canUpload}
                onClick={uploadFiles}
                title={canUpload ? undefined : "Confirm source and target, then add files"}
                type="button"
              >
                <span aria-hidden="true">⇧</span> Upload Files
              </button>
              <button
                className="button primary"
                disabled={!hasUploaded || converted}
                onClick={previewConversion}
                title={
                  !hasUploaded
                    ? "Upload files first"
                    : converted
                      ? "Preview already complete"
                      : undefined
                }
                type="button"
              >
                <span aria-hidden="true">⚙</span> Convert Items
              </button>
              <button
                className="button download-button"
                disabled={!downloadAvailable}
                title="Available once conversion is implemented in a later phase"
                type="button"
              >
                <span aria-hidden="true">⇩</span> Download Converted Files
              </button>
            </div>
          </div>
          <p className="egress-banner">
            <svg aria-hidden="true" className="egress-icon" viewBox="0 0 16 16">
              <circle cx="8" cy="8" r="6.25" fill="none" stroke="currentColor" strokeWidth="1.5" />
              <path d="M8 7.2v3.5" stroke="currentColor" strokeLinecap="round" strokeWidth="1.5" />
              <circle cx="8" cy="4.75" r=".8" fill="currentColor" />
            </svg>
            Converted objects and their context are sent to Devin cloud for conversion.
          </p>
          <div className="convert-content">
            <div
              className="drop-zone"
              onDragOver={(event) => event.preventDefault()}
              onDrop={acceptDrop}
            >
              <input
                accept=".sql,.ddl,.txt,.zip,.xml,.dtsx,.dsx,.py,.sh,.scala,.json,.yaml,.yml,.infa"
                aria-label="Choose migration files"
                className="visually-hidden"
                multiple
                onChange={(event) => {
                  if (event.target.files) {
                    addFiles(event.target.files);
                    event.target.value = "";
                  }
                }}
                ref={fileInput}
                type="file"
              />
              <span aria-hidden="true" className="upload-cloud">⇧</span>
              <strong>Drag and drop files here</strong>
              <span>or click to select files</span>
              <button className="browse-button" onClick={() => fileInput.current?.click()} type="button">
                Browse files
              </button>
              <small>Supported: SQL, DDL, TXT, ETL exports, ZIP (Max 100 MB)</small>
            </div>
            <div className="inventory">
              <div className="inventory-title">
                <strong>File Upload &amp; Conversion Status</strong>
                {files.length > 0 && <span>{files.length} file{files.length === 1 ? "" : "s"}</span>}
              </div>
              <div className="table-scroll inventory-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>File Name</th>
                      <th>Size</th>
                      <th>Type</th>
                      <th>Status</th>
                      <th>Message</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {files.length === 0 ? (
                      <tr>
                        <td className="empty-table" colSpan={6}>No files selected.</td>
                      </tr>
                    ) : (
                      files.map((row) => (
                        <tr key={row.id}>
                          <td className="file-name">{row.file.name}</td>
                          <td>{formatSize(row.file.size)}</td>
                          <td>{extensionOf(row.file.name) || "—"}</td>
                          <td>
                            <span className={`file-status ${row.status.toLowerCase().replaceAll(" ", "-")}`}>
                              {row.status}
                            </span>
                          </td>
                          <td>{row.message}</td>
                          <td>
                            <button
                              aria-label={`Remove ${row.file.name}`}
                              className="row-action"
                              onClick={() => removeFile(row.id)}
                              type="button"
                            >
                              ×
                            </button>
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
              {notice && <p className="inline-notice" role="status">{notice}</p>}
            </div>
          </div>
        </section>
      </main>

      <footer className="statusbar">
        <span>MigraAI v{health?.version ?? "0.1.0"}</span>
        <span className="status-separator">|</span>
        <span>Local enterprise workspace</span>
        <div className="status-items">
          <span><i className="status-dot" /> Ready</span>
          <span><i className="status-dot" /> Local Mode</span>
          <span>
            <i className={`status-dot ${health?.devin_configured ? "" : "muted-dot"}`} />
            Devin: {health?.devin_configured ? "configured" : "not configured"}
          </span>
          <span className="footer-note">Uploads are deleted on exit</span>
        </div>
      </footer>

      <SettingsDialog
        onClose={() => setSettingsOpen(false)}
        onSave={saveSettings}
        open={settingsOpen}
        settings={settings}
      />
    </div>
  );
}
