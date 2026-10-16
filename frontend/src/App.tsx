import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";

import {
  apiRequest,
  confirmSession,
  exchangeLaunchToken,
  getCurrentSession,
  getPlan,
  getUploads,
  type HealthResponse,
  type Platform,
  type PlatformResponse,
  type SessionView,
  type SettingsResponse,
  type SettingsUpdate,
  type UploadRecord,
  distinctUploadRoots,
} from "./api";
import { KnowledgeBaseSettings } from "./KnowledgeBaseSettings";
import { ConvertPanel } from "./ConvertPanel";
import { InventoryView } from "./InventoryView";
import { PlanningPanel } from "./PlanningPanel";
import { ResourcesPanel } from "./ResourcesPanel";
import { UploadPanel } from "./UploadPanel";
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
  const [uploads, setUploads] = useState<UploadRecord[]>([]);
  const [inventoryRevision, setInventoryRevision] = useState(0);
  const [workspaceTab, setWorkspaceTab] = useState<"planning" | "inventory">("planning");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const mainRef = useRef<HTMLElement | null>(null);
  const planningSectionRef = useRef<HTMLElement | null>(null);
  const updateUploads = useCallback((next: UploadRecord[]) => setUploads(next), []);
  const refreshInventory = useCallback(() => {
    setInventoryRevision((current) => current + 1);
    void getPlan()
      .then((nextPlan) => {
        setSession((current) => {
          if (
            !current ||
            (current.plan && current.plan.inventory_stale === nextPlan.inventory_stale)
          ) {
            return current;
          }
          return { ...current, plan: nextPlan };
        });
      })
      .catch(() => undefined);
  }, []);
  const objectCount = uploads.reduce(
    (total, row) => total + (row.selected ? row.object_count : 0),
    0,
  );

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
      let restoredPlan: SessionView["plan"] = null;
      let restoredUploads: UploadRecord[] = [];
      if (currentSession?.confirmed && currentSession.source && currentSession.target) {
        [restoredPlan, restoredUploads] = await Promise.all([
          getPlan().catch(() => null),
          getUploads().catch(() => []),
        ]);
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
        setUploads(restoredUploads);
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
      setUploads([]);
      setInventoryRevision((current) => current + 1);
      setWorkspaceTab("planning");
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
      setNotice("Source and target confirmed. Migration planning has started.");
    } catch (confirmError) {
      setError(confirmError instanceof Error ? confirmError.message : "Could not confirm systems.");
    } finally {
      setConfirming(false);
    }
  }

  async function saveSettings(updates: SettingsUpdate) {
    const updated = await apiRequest<SettingsResponse>("/api/settings", {
      method: "PUT",
      body: updates,
    });
    setSettings(updated);
    const updatedHealth = await apiRequest<HealthResponse>("/api/health").catch(() => null);
    setHealth((current) =>
      updatedHealth ??
        (current ? { ...current, devin_configured: updated.devin_pat.configured } : current),
    );
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
          aria-label="Migration Planning"
          className="panel planning-panel"
          ref={planningSectionRef}
        >
          <div className="panel-heading workspace-heading">
            <div aria-label="Workspace views" className="workspace-tabs" role="tablist">
              <button
                aria-controls="planning-panel"
                aria-selected={workspaceTab === "planning"}
                className="workspace-tab"
                id="planning-tab"
                onClick={() => setWorkspaceTab("planning")}
                role="tab"
                type="button"
              >
                <span className="section-number">2</span>
                Migration Planning
              </button>
              <button
                aria-controls="inventory-panel"
                aria-selected={workspaceTab === "inventory"}
                className="workspace-tab"
                disabled={objectCount === 0}
                id="inventory-tab"
                onClick={() => setWorkspaceTab("inventory")}
                role="tab"
                type="button"
              >
                Inventory
                {objectCount > 0 && <span className="tab-count">{objectCount}</span>}
              </button>
            </div>
            <span className="phase-chip">Phase 3</span>
          </div>
          {workspaceTab === "planning" ? (
            <div
              aria-labelledby="planning-tab"
              className="workspace-tabpanel planning-tabpanel"
              id="planning-panel"
              role="tabpanel"
            >
              <PlanningPanel
                confidenceThreshold={settings.confidence_threshold}
                devinConfigured={health?.devin_configured ?? false}
                session={sessionConfirmed ? session : null}
                sourceName={
                  platforms.sources.find((item) => item.id === session?.source)?.name ?? ""
                }
                targetName={
                  platforms.targets.find((item) => item.id === session?.target)?.name ?? ""
                }
              />
            </div>
          ) : (
            <div
              aria-labelledby="inventory-tab"
              className="workspace-tabpanel inventory-tabpanel"
              id="inventory-panel"
              role="tabpanel"
            >
              <InventoryView
                revision={inventoryRevision}
                uploads={uploads}
              />
            </div>
          )}
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
          <ResourcesPanel confirmed={sessionConfirmed} refreshKey={inventoryRevision} />
        </section>

        <section aria-labelledby="convert-heading" className="panel convert-panel">
          <div className="panel-heading convert-heading">
            <SectionTitle
              description="1. Browse or drop files · 2. Upload Files · 3. Convert Items"
              headingId="convert-heading"
              number="4"
              title="Convert Objects"
            />
          </div>
          <p className="egress-banner">
            <svg aria-hidden="true" className="egress-icon" viewBox="0 0 16 16">
              <circle cx="8" cy="8" r="6.25" fill="none" stroke="currentColor" strokeWidth="1.5" />
              <path d="M8 7.2v3.5" stroke="currentColor" strokeLinecap="round" strokeWidth="1.5" />
              <circle cx="8" cy="4.75" r=".8" fill="currentColor" />
            </svg>
            {health?.conversion_configured
              ? health.devin_configured
                ? "Converted objects and their context are sent to Devin cloud for conversion."
                : "Local mock mode is active; conversion code stays on this machine."
              : "Add a Devin API key to enable conversion; code is not sent before consent."}
          </p>
          <UploadPanel
            confirmed={sessionConfirmed}
            onInventoryChanged={refreshInventory}
            onUploadsChange={updateUploads}
            uploads={uploads}
          />
          <ConvertPanel
            confirmed={sessionConfirmed}
            refreshKey={inventoryRevision}
            source={session?.source ?? ""}
            target={session?.target ?? ""}
            uploadRoots={distinctUploadRoots(uploads)}
          />
          {notice && <p className="inline-notice" role="status">{notice}</p>}
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
            <i className={`status-dot ${health?.conversion_configured ? "" : "muted-dot"}`} />
            Devin: {health?.conversion_configured ? "configured" : "not configured"}
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
