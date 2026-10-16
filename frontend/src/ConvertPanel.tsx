import { useCallback, useEffect, useRef, useState } from "react";

import {
  cancelConversionJob,
  downloadConvertedPackage,
  downloadReport,
  getConversionDetail,
  getConversionJob,
  getConversions,
  postConvertEstimate,
  startConversion,
  type ConversionDetailResponse,
  type ConversionJobStatus,
  type ConversionRow,
  type ConvertEstimate,
} from "./api";
import { acuCapWarning, formatAcuCap } from "./acuCap";
import { ConvertConsentDialog } from "./ConvertConsentDialog";
import { ConversionDetail } from "./ConversionDetail";

const ACTIVE_JOB_KEY = "migraai.activeConversionJob";
const CHECKS = [
  "syntax",
  "lint",
  "semantic",
  "rule",
  "dependency",
  "object_reference",
  "security",
];
const NO_DEVIN_MESSAGE =
  "Devin isn't configured. Add a Devin API key in Settings to convert; nothing was sent.";

function isTerminal(state: string): boolean {
  return ["done", "failed", "cancelled"].includes(state);
}

function scrollToMainElement(element: HTMLElement | null) {
  const main = element?.closest<HTMLElement>("main.dashboard");
  if (!element || !main || typeof main.scrollTo !== "function") {
    return;
  }
  const top = main.scrollTop + element.getBoundingClientRect().top - main.getBoundingClientRect().top;
  const prefersReducedMotion =
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
  main.scrollTo({ top, behavior: prefersReducedMotion ? "auto" : "smooth" });
}

export function ConvertPanel({
  confirmed,
  source,
  target,
  refreshKey,
  settingsRevision = 0,
  uploadCount = 0,
  uploadRoots,
}: {
  confirmed: boolean;
  source: string;
  target: string;
  refreshKey: number;
  settingsRevision?: number;
  uploadCount?: number;
  uploadRoots?: string[];
}) {
  const [estimate, setEstimate] = useState<ConvertEstimate | null>(null);
  const [dialogEstimate, setDialogEstimate] = useState<ConvertEstimate | null>(null);
  const [conversionObjectIds, setConversionObjectIds] = useState<string[] | undefined>();
  const [loadingEstimate, setLoadingEstimate] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [starting, setStarting] = useState(false);
  const [jobId, setJobId] = useState("");
  const [job, setJob] = useState<ConversionJobStatus | null>(null);
  const [rows, setRows] = useState<ConversionRow[]>([]);
  const [selectedObjectIds, setSelectedObjectIds] = useState<string[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ConversionDetailResponse | null>(null);
  const [error, setError] = useState("");
  const [cancelConfirm, setCancelConfirm] = useState(false);
  const [statusFilter, setStatusFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [failedCheck, setFailedCheck] = useState("");
  const [search, setSearch] = useState("");
  const [packageName, setPackageName] = useState("");
  const [listRevision, setListRevision] = useState(0);
  const activeJobState = job?.state;
  const resultsRef = useRef<HTMLElement | null>(null);
  const detailRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setPackageName(`migraai-${source}-to-${target}`);
  }, [source, target]);

  useEffect(() => {
    let active = true;
    if (!confirmed) {
      setEstimate(null);
      return () => {
        active = false;
      };
    }
    setLoadingEstimate(true);
    void postConvertEstimate()
      .then((next) => {
        if (active) {
          setEstimate(next);
        }
      })
      .catch(() => {
        if (active) {
          setEstimate(null);
        }
      })
      .finally(() => {
        if (active) {
          setLoadingEstimate(false);
        }
      });
    return () => {
      active = false;
    };
  }, [confirmed, refreshKey, settingsRevision, source, target]);

  useEffect(() => {
    const savedId = window.localStorage.getItem(ACTIVE_JOB_KEY);
    if (!savedId) {
      return;
    }
    let active = true;
    void getConversionJob(savedId)
      .then((status) => {
        if (active) {
          setJobId(savedId);
          setJob(status);
        }
      })
      .catch(() => {
        window.localStorage.removeItem(ACTIVE_JOB_KEY);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (activeJobState && !["queued", "running"].includes(activeJobState)) {
      setCancelConfirm(false);
    }
  }, [activeJobState]);

  useEffect(() => {
    if (!jobId) {
      return;
    }
    let active = true;
    let timer = 0;
    const poll = async () => {
      try {
        const next = await getConversionJob(jobId);
        if (!active) {
          return;
        }
        setJob(next);
        setListRevision((revision) => revision + 1);
        if (isTerminal(next.state)) {
          window.requestAnimationFrame(() => scrollToMainElement(resultsRef.current));
        } else {
          timer = window.setTimeout(() => void poll(), 1000);
        }
      } catch {
        if (active) {
          setError("Could not refresh conversion progress.");
        }
      }
    };
    void poll();
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [jobId]);

  const refreshRows = useCallback(async () => {
    if (!confirmed) {
      setRows([]);
      return;
    }
    try {
      const result = await getConversions({
        status: statusFilter,
        type: typeFilter,
        failed_check: failedCheck,
        search,
        page: 1,
        page_size: 200,
      });
      const items = Array.isArray(result.items) ? result.items : [];
      setRows(items);
      setSelectedObjectIds((current) =>
        current.filter((objectId) => items.some((item) => item.object_id === objectId)),
      );
      setSelectedId((current) =>
        items.some((item) => item.object_id === current)
          ? current
          : items[0]?.object_id ?? null,
      );
    } catch {
      setRows([]);
    }
  }, [confirmed, failedCheck, search, statusFilter, typeFilter]);

  useEffect(() => {
    void refreshRows();
  }, [listRevision, refreshRows]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let active = true;
    void getConversionDetail(selectedId)
      .then((next) => {
        if (active) {
          setDetail(next);
        }
      })
      .catch(() => {
        if (active) {
          setDetail(null);
        }
      });
    return () => {
      active = false;
    };
  }, [selectedId, listRevision]);

  async function openConsent() {
    setError("");
    if (!estimate || estimate.objects < 1) {
      return;
    }
    try {
      const nextEstimate = await postConvertEstimate();
      setEstimate(nextEstimate);
      if (nextEstimate.objects < 1) {
        return;
      }
      if (!nextEstimate.devin_configured) {
        setError(NO_DEVIN_MESSAGE);
        return;
      }
      setDialogEstimate(nextEstimate);
      setConversionObjectIds(undefined);
      setDialogOpen(true);
    } catch (estimateError) {
      setError(
        estimateError instanceof Error ? estimateError.message : "Could not estimate conversion.",
      );
    }
  }

  async function openSelectedConsent() {
    const objectIds = [...selectedObjectIds];
    if (objectIds.length === 0) {
      return;
    }
    setError("");
    try {
      const nextEstimate = await postConvertEstimate(objectIds);
      if (!nextEstimate.devin_configured) {
        setError(NO_DEVIN_MESSAGE);
        return;
      }
      setDialogEstimate(nextEstimate);
      setConversionObjectIds(objectIds);
      setDialogOpen(true);
    } catch (estimateError) {
      setError(estimateError instanceof Error ? estimateError.message : "Could not estimate conversion.");
    }
  }

  async function confirmConversion() {
    setStarting(true);
    setError("");
    try {
      const started = conversionObjectIds
        ? await startConversion(conversionObjectIds)
        : await startConversion();
      window.localStorage.setItem(ACTIVE_JOB_KEY, started.job_id);
      setJobId(started.job_id);
      setDialogOpen(false);
    } catch (startError) {
      setError(startError instanceof Error ? startError.message : "Could not start conversion.");
    } finally {
      setStarting(false);
    }
  }

  async function cancelJob() {
    if (!jobId) {
      return;
    }
    setError("");
    try {
      setJob(await cancelConversionJob(jobId));
      setCancelConfirm(false);
      setListRevision((revision) => revision + 1);
    } catch (cancelError) {
      setError(cancelError instanceof Error ? cancelError.message : "Could not cancel conversion.");
    }
  }

  const active = job !== null && ["queued", "running"].includes(job.state);
  const canReconvert = job !== null && ["done", "cancelled"].includes(job.state);
  const types = Array.from(new Set(rows.map((row) => row.type))).sort();
  const includedCount = (job?.counts.converted ?? 0) + (job?.counts.expert_review ?? 0);
  const rootCount = uploadRoots?.length ?? uploadCount;
  const multipleUploads = rootCount > 1;
  const downloadReady =
    job !== null && ["done", "cancelled"].includes(job.state) && includedCount > 0;
  const downloadReason = !job
    ? "Run a conversion before downloading."
    : !["done", "cancelled"].includes(job.state)
      ? "Download is available when the conversion is done or cancelled."
      : "No converted or expert-review objects are available to download.";

  async function handleDownload() {
    setError("");
    try {
      await downloadConvertedPackage(multipleUploads ? packageName : undefined);
    } catch (downloadError) {
      setError(downloadError instanceof Error ? downloadError.message : "Download failed.");
    }
  }

  async function handleReport(format: "md" | "pdf") {
    setError("");
    try {
      await downloadReport(format);
    } catch (reportError) {
      setError(reportError instanceof Error ? reportError.message : "Report download failed.");
    }
  }

  function selectResult(objectId: string) {
    setSelectedId(objectId);
    window.requestAnimationFrame(() => scrollToMainElement(detailRef.current));
  }

  return (
    <div className="convert-workspace">
      <div className="convert-toolbar">
        <div className="convert-estimate" aria-live="polite">
          {estimate ? (
            <>
              <strong>{estimate.objects}</strong> objects · {estimate.batches} batches · up to{" "}
              {estimate.max_sessions} sessions ·{" "}
              <span>{formatAcuCap(estimate)}</span>
              {acuCapWarning(estimate) && (
                <span className="acu-cap-warning">{acuCapWarning(estimate)}</span>
              )}
            </>
          ) : confirmed && loadingEstimate ? (
            "Estimating selected objects…"
          ) : (
            "Confirm a source and target and select parsed objects to convert."
          )}
        </div>
        <div className="convert-actions">
          <button
            className="button primary"
            disabled={
              !confirmed ||
              loadingEstimate ||
              !estimate ||
              (estimate.objects ?? 0) < 1 ||
              starting ||
              active
            }
            onClick={openConsent}
            title={
              !confirmed
                ? "Upload files first"
                : loadingEstimate
                  ? "Waiting for the conversion estimate."
                  : undefined
            }
            type="button"
          >
            {starting
              ? "Starting…"
              : active
                ? "Converting…"
                : loadingEstimate
                  ? "Estimating…"
                  : "Convert Items"}
          </button>
          <button
            className="button download-button"
            disabled={!downloadReady}
            onClick={() => void handleDownload()}
            title={downloadReady ? undefined : downloadReason}
            type="button"
          >
            ⇩ Download Converted Files
          </button>
          {multipleUploads && (
            <label className="package-name-field">
              Package name
              <input
                aria-label="Package name"
                maxLength={80}
                onChange={(event) => setPackageName(event.target.value)}
                value={packageName}
              />
            </label>
          )}
          <a
            className="report-download-link"
            href="/api/report.md"
            onClick={(event) => {
              event.preventDefault();
              void handleReport("md");
            }}
          >
            Report (MD)
          </a>
          <a
            className="report-download-link"
            href="/api/report.pdf"
            onClick={(event) => {
              event.preventDefault();
              void handleReport("pdf");
            }}
          >
            Report (PDF)
          </a>
        </div>
      </div>
      {error && <p className="inline-error conversion-error" role="alert">{error}</p>}
      {job && (
        <section aria-label="Conversion progress" className="conversion-progress">
          <div className="conversion-progress-heading">
            <div>
              <strong>Conversion {job.state}</strong>
              <span>{job.object_count} objects · {job.batch_count} batches</span>
            </div>
            {active && !cancelConfirm && (
              <button className="button danger-button" onClick={() => setCancelConfirm(true)}>
                Cancel
              </button>
            )}
          </div>
          {cancelConfirm && active && (
            <div className="cancel-confirm" role="alertdialog" aria-label="Confirm cancellation">
              <span>Cancel this conversion?</span>
              <button className="button secondary" onClick={() => setCancelConfirm(false)}>
                Keep converting
              </button>
              <button className="button danger-button" onClick={() => void cancelJob()}>
                Cancel conversion
              </button>
            </div>
          )}
          <div className="conversion-counts">
            {["converted", "escalated", "expert_review", "failed"].map((name) => (
              <span key={name}>
                <strong>{job.counts[name] ?? 0}</strong> {name.replace("_", " ")}
              </span>
            ))}
          </div>
          {Object.keys(job.kept_base_object_ids ?? {}).length > 0 && (
            <div className="conversion-kept-base-note" role="note">
              {Object.keys(job.kept_base_object_ids ?? {}).length} selected objects kept their
              earlier result (re-conversion cancelled or not usable)
            </div>
          )}
          <div className="conversion-batch-grid">
            {job.batches.map((batch) => (
              <div className="conversion-batch" key={batch.index}>
                <strong>Batch {batch.index + 1}</strong>
                {["A", "B", "C", "escalation"].map((voter) => (
                  <span className={`voter-state state-${batch.voters[voter] ?? "queued"}`} key={voter}>
                    {voter}: {batch.voters[voter] ?? "queued"}
                  </span>
                ))}
              </div>
            ))}
          </div>
        </section>
      )}
      <section aria-label="Conversion results" className="conversion-results" ref={resultsRef}>
        <div className="conversion-filters">
          <label>
            Status
            <select aria-label="Filter by status" onChange={(event) => setStatusFilter(event.target.value)} value={statusFilter}>
              <option value="">All</option>
              {["converted", "expert_review", "failed", "cancelled", "pending"].map((value) => (
                <option key={value} value={value}>{value.replace("_", " ")}</option>
              ))}
            </select>
          </label>
          <label>
            Type
            <select aria-label="Filter by type" onChange={(event) => setTypeFilter(event.target.value)} value={typeFilter}>
              <option value="">All</option>
              {types.map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
          </label>
          <label>
            Failed check
            <select aria-label="Filter by failed check" onChange={(event) => setFailedCheck(event.target.value)} value={failedCheck}>
              <option value="">Any</option>
              {CHECKS.map((value) => <option key={value} value={value}>{value.replace("_", " ")}</option>)}
            </select>
          </label>
          <label className="conversion-search">
            Search
            <input
              aria-label="Search converted objects"
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Object name"
              value={search}
            />
          </label>
        </div>
        <div className="conversion-results-scroll">
          <div className="conversion-selection-controls">
            <button
              disabled={active || !canReconvert || selectedObjectIds.length === 0}
              onClick={() => void openSelectedConsent()}
              type="button"
            >
              Re-convert selected ({selectedObjectIds.length})
            </button>
            <button
              disabled={active || !canReconvert}
              onClick={() => setSelectedObjectIds(rows
                .filter((row) => row.status === "expert_review")
                .map((row) => row.object_id))}
              type="button"
            >
              Select objects needing review
            </button>
          </div>
          <table className="conversion-table">
            <thead>
              <tr>
                <th>Select</th><th>Object</th><th>Type</th><th>Confidence</th><th>Agreement</th>
                {CHECKS.map((check) => <th key={check}>{check.replace("_", " ")}</th>)}
                <th>Escalated</th><th>Expert review</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr
                  aria-selected={selectedId === row.object_id}
                  className={selectedId === row.object_id ? "selected-conversion-row" : ""}
                  key={row.object_id}
                  onClick={() => selectResult(row.object_id)}
                >
                  <td>
                    <input
                      aria-label={`Select ${row.qualified_name} for re-conversion`}
                      checked={selectedObjectIds.includes(row.object_id)}
                      disabled={active || !canReconvert}
                      onChange={(event) => {
                        setSelectedObjectIds((current) => event.target.checked
                          ? [...new Set([...current, row.object_id])]
                          : current.filter((objectId) => objectId !== row.object_id));
                      }}
                      onClick={(event) => event.stopPropagation()}
                      type="checkbox"
                    />
                  </td>
                  <td>
                    <button
                      className="conversion-object-link"
                      onClick={(event) => {
                        event.stopPropagation();
                        selectResult(row.object_id);
                      }}
                    >
                      {row.qualified_name}
                    </button>
                  </td>
                  <td>{row.type}</td>
                  <td>{row.confidence === null ? "—" : `${Math.round(row.confidence * 100)}%`}</td>
                  <td>{row.agreement === null ? "—" : `${Math.round(row.agreement * 100)}%`}</td>
                  {CHECKS.map((check) => {
                    const state = row.checks[check] ?? "not_applicable";
                    return (
                      <td key={check}>
                        <span aria-label={`${check}: ${state}`} className={`check-chip check-${state}`} title={`${check}: ${state}`}>
                          {state === "not_applicable" ? "n/a" : state === "passed" ? "✓" : "!"}
                        </span>
                      </td>
                    );
                  })}
                  <td>{row.escalated ? "Yes" : "No"}</td>
                  <td>{row.status === "expert_review" ? "Yes" : "No"}</td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr><td className="conversion-empty-row" colSpan={CHECKS.length + 7}>No conversion results match these filters.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
      <div className="conversion-detail-anchor" ref={detailRef}>
        <ConversionDetail detail={detail} />
      </div>
      <ConvertConsentDialog
        estimate={dialogEstimate ?? estimate}
        onCancel={() => {
          setDialogOpen(false);
          setDialogEstimate(null);
          setConversionObjectIds(undefined);
        }}
        onConfirm={() => void confirmConversion()}
        open={dialogOpen}
      />
    </div>
  );
}
