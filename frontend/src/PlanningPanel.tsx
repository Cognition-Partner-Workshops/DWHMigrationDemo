import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import {
  cancelPlanJob,
  getPlan,
  startPlan,
  type PlanSection,
  type PlanStatus,
  type SessionView,
} from "./api";

const TERMINAL_STATES = new Set(["done", "failed_fallback", "cancelled", "failed"]);

function formatElapsed(seconds: number): string {
  if (seconds < 60) {
    return `${Math.round(seconds)}s`;
  }
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

function formatDate(value: string | null | undefined): string {
  if (!value) {
    return "date unavailable";
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString();
}

function needsReview(section: PlanSection, lowConfidence: boolean): boolean {
  return (
    lowConfidence ||
    Boolean(section.manual_review) ||
    Boolean(section.unverified_citations) ||
    section.citations.some((citation) => citation.unverified)
  );
}

export function PlanningPanel({
  session,
  sourceName,
  targetName,
  confidenceThreshold,
}: {
  session: SessionView | null;
  sourceName: string;
  targetName: string;
  confidenceThreshold: number;
}) {
  const [plan, setPlan] = useState<PlanStatus | null>(session?.plan ?? null);
  const [selectedSection, setSelectedSection] = useState("A");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setPlan(session?.plan ?? null);
    setSelectedSection("A");
    setError("");
  }, [session?.source, session?.target, session?.plan]);

  useEffect(() => {
    const currentPlan = plan;
    if (!session || !currentPlan || TERMINAL_STATES.has(currentPlan.state)) {
      return;
    }
    const initialState = currentPlan.state;
    let active = true;
    let timer: number | undefined;

    async function poll() {
      let nextState = initialState;
      try {
        const next = await getPlan();
        nextState = next.state;
        if (active) {
          setPlan(next);
          setError("");
        }
      } catch (pollError) {
        if (active) {
          setError(pollError instanceof Error ? pollError.message : "Could not refresh the plan.");
        }
      }
      if (active && !TERMINAL_STATES.has(nextState)) {
        timer = window.setTimeout(poll, 3000);
      }
    }

    timer = window.setTimeout(poll, 3000);
    return () => {
      active = false;
      if (timer !== undefined) {
        window.clearTimeout(timer);
      }
    };
  }, [session, plan]);

  async function requestPlan(regenerate: boolean) {
    setBusy(true);
    setError("");
    try {
      setPlan(await startPlan(regenerate));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Could not start planning.");
    } finally {
      setBusy(false);
    }
  }

  async function cancelJob() {
    setBusy(true);
    setError("");
    try {
      setPlan(await cancelPlanJob());
    } catch (cancelError) {
      setError(cancelError instanceof Error ? cancelError.message : "Could not cancel planning.");
    } finally {
      setBusy(false);
    }
  }

  const isRunning = Boolean(plan && !TERMINAL_STATES.has(plan.state));
  const sections = plan?.plan?.sections ?? [];
  const activeSection =
    sections.find((section) => section.id === selectedSection) ?? sections[0] ?? null;
  const lowConfidence =
    plan?.plan?.overall_confidence !== null &&
    plan?.plan?.overall_confidence !== undefined &&
    plan.plan.overall_confidence < confidenceThreshold;
  const fallback = plan?.state === "failed_fallback" || plan?.origin === "local_draft";
  const showSavedPlan = plan?.origin === "devin" && Boolean(plan.plan);

  return (
    <div className="planning-content">
      {!session ? (
        <div className="empty-state">
          <span aria-hidden="true" className="empty-icon">▤</span>
          <strong>Migration plan will appear here</strong>
          <p>Choose a source and target, then confirm to begin planning.</p>
        </div>
      ) : (
        <>
          <div className="planning-summary">
            <span>
              <strong>{sourceName || session.source}</strong>
              <small>Source</small>
            </span>
            <b aria-hidden="true">→</b>
            <span>
              <strong>{targetName || session.target}</strong>
              <small>Target</small>
            </span>
            <span className="generated-note">
              {plan?.stale ? "Plan uses an earlier KB version" : "Platform migration plan"}
            </span>
          </div>
          <div className="planning-toolbar">
            <div aria-live="polite" className="plan-progress">
              <span className={`plan-state-dot ${isRunning ? "active" : ""}`} />
              {plan?.origin === "local_draft" ? (
                <strong>Local draft</strong>
              ) : (
                <>
                  <strong>
                    {plan
                      ? plan.progress.phase.replaceAll("_", " ")
                      : "Preparing plan"}
                  </strong>
                  <span>{formatElapsed(plan?.progress.elapsed_s ?? 0)}</span>
                </>
              )}
              {plan?.progress.devin_url && (
                <a href={plan.progress.devin_url} rel="noreferrer" target="_blank">
                  Open Devin session
                </a>
              )}
            </div>
            <div className="plan-actions">
              {isRunning && (
                <button
                  className="button secondary compact-button"
                  disabled={busy}
                  onClick={() => void cancelJob()}
                  type="button"
                >
                  Cancel
                </button>
              )}
              {plan?.plan && (
                <a className="button secondary compact-button" download href="/api/plan.pdf">
                  Download PDF
                </a>
              )}
              {showSavedPlan && (
                <button
                  className="button secondary compact-button"
                  disabled={busy}
                  onClick={() => {
                    if (
                      window.confirm(
                        "Regenerate this plan? This starts a new Devin session and uses ACUs.",
                      )
                    ) {
                      void requestPlan(true);
                    }
                  }}
                  type="button"
                >
                  Regenerate
                </button>
              )}
            </div>
          </div>
          {fallback && (
            <div className="plan-fallback" role="status">
              <div>
                <strong>Local draft (Devin unavailable)</strong>
                <span>{plan.reason || "Devin is unavailable."}</span>
              </div>
              <button
                className="button primary compact-button"
                disabled={busy}
                onClick={() => void requestPlan(true)}
                type="button"
              >
                Retry
              </button>
            </div>
          )}
          {showSavedPlan && (
            <div className="saved-plan-note">
              Saved plan · KB v{plan.kb_version ?? "—"} · {formatDate(plan.created_at)}
            </div>
          )}
          {lowConfidence && (
            <div className="plan-review-notice" role="note">
              Manual review recommended — overall confidence is below the configured threshold.
            </div>
          )}
          {error && <p className="planning-error" role="alert">{error}</p>}
          {activeSection ? (
            <div className="plan-result">
              <nav aria-label="Plan sections" className="plan-index" role="tablist">
                {sections.map((section) => (
                  <button
                    aria-selected={activeSection.id === section.id}
                    className={activeSection.id === section.id ? "selected" : ""}
                    id={`plan-tab-${section.id}`}
                    key={section.id}
                    onClick={() => setSelectedSection(section.id)}
                    role="tab"
                    type="button"
                  >
                    <span>{section.id}</span>
                    <span className="plan-index-title">{section.title}</span>
                    {needsReview(section, lowConfidence) && (
                      <span aria-label="Manual review" className="review-dot" />
                    )}
                  </button>
                ))}
              </nav>
              <article
                aria-labelledby={`plan-tab-${activeSection.id}`}
                className="plan-section-detail"
                role="tabpanel"
              >
                <div className="plan-section-heading">
                  <h3>{activeSection.id}. {activeSection.title}</h3>
                  <div className="plan-section-badges">
                    {activeSection.platform_level_estimate && (
                      <span className="estimate-badge">Platform-level estimate</span>
                    )}
                    {needsReview(activeSection, lowConfidence) && (
                      <span className="manual-review-badge">Manual review</span>
                    )}
                  </div>
                </div>
                <div className="markdown-content">
                  <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml>
                    {activeSection.content}
                  </ReactMarkdown>
                </div>
                {activeSection.citations.length > 0 && (
                  <div className="plan-citations">
                    <strong>Citations</strong>
                    <ol>
                      {activeSection.citations.map((citation, index) => {
                        const reference = citation.url
                          ? citation.url
                          : `/api/kb/search?q=${encodeURIComponent(citation.note || `KB-${citation.kb_id}`)}&source=${encodeURIComponent(session.source)}&target=${encodeURIComponent(session.target)}&k=1`;
                        return (
                          <li key={`${citation.kb_id ?? citation.url}-${index}`}>
                            <a href={reference} rel="noreferrer" target="_blank">
                              {citation.url ? citation.url : `KB-${citation.kb_id}`}
                            </a>
                            <span>{citation.note}</span>
                            {citation.unverified && (
                              <em className="citation-unverified">Unverified</em>
                            )}
                          </li>
                        );
                      })}
                    </ol>
                  </div>
                )}
              </article>
            </div>
          ) : (
            <div className="plan-loading-state">
              {isRunning ? "Building your migration plan…" : "No plan content is available."}
            </div>
          )}
        </>
      )}
    </div>
  );
}
