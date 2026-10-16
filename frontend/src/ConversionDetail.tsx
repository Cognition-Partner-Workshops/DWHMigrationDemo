import { useState } from "react";

import type {
  ConversionCandidate,
  ConversionDetailResponse,
  ConversionFile,
} from "./api";

type DiffLine = { kind: "same" | "removed" | "added"; text: string };

function lineDiff(base: string, next: string): DiffLine[] {
  const left = base.split("\n");
  const right = next.split("\n");
  let prefix = 0;
  while (prefix < left.length && prefix < right.length && left[prefix] === right[prefix]) {
    prefix += 1;
  }
  let suffix = 0;
  while (
    suffix < left.length - prefix &&
    suffix < right.length - prefix &&
    left[left.length - 1 - suffix] === right[right.length - 1 - suffix]
  ) {
    suffix += 1;
  }
  return [
    ...left.slice(0, prefix).map((text) => ({ kind: "same" as const, text })),
    ...left.slice(prefix, left.length - suffix).map((text) => ({ kind: "removed" as const, text })),
    ...right.slice(prefix, right.length - suffix).map((text) => ({ kind: "added" as const, text })),
    ...(suffix ? left.slice(left.length - suffix) : []).map((text) => ({
      kind: "same" as const,
      text,
    })),
  ];
}

function matchingFinalFile(
  file: ConversionFile,
  candidateFiles: ConversionFile[],
  finalFiles: ConversionFile[],
  index: number,
): ConversionFile | null {
  const byPath = finalFiles.find((item) => item.rel_path === file.rel_path);
  if (byPath) {
    return byPath;
  }

  const byFormat = file.etl_format_id
    ? finalFiles.filter(
        (item) => item.etl_format_id === file.etl_format_id && item.language === file.language,
      )
    : [];
  const matches = byFormat.length
    ? byFormat
    : finalFiles.filter((item) => item.language === file.language);
  const groupIndex = candidateFiles.slice(0, index).filter(
    (item) =>
      item.language === file.language &&
      (!byFormat.length || item.etl_format_id === file.etl_format_id),
  ).length;
  return matches[groupIndex] ?? null;
}

function candidateFor(
  detail: ConversionDetailResponse,
  tab: string,
): ConversionCandidate | null {
  if (tab === "Final") {
    return {
      files: detail.final.files,
      self_confidence: null,
      state: "done",
      error: null,
    };
  }
  return detail.candidates[tab as "A" | "B" | "C"];
}

export function ConversionDetail({
  detail,
}: {
  detail: ConversionDetailResponse | null;
}) {
  const [tab, setTab] = useState("Final");
  const [copied, setCopied] = useState("");

  if (!detail) {
    return <div className="conversion-detail-empty">Select an object to inspect its conversion.</div>;
  }

  const candidate = candidateFor(detail, tab);
  const files = candidate?.files ?? [];
  async function copyFile(file: ConversionFile) {
    try {
      await navigator.clipboard.writeText(file.content);
      setCopied(file.rel_path);
      window.setTimeout(() => setCopied(""), 1500);
    } catch {
      setCopied("");
    }
  }

  return (
    <section aria-label="Conversion detail" className="conversion-detail">
      <div aria-label="Conversion candidates" className="conversion-detail-tabs" role="tablist">
        {["Final", "A", "B", "C"].map((name) => (
          <button
            aria-selected={tab === name}
            className="conversion-detail-tab"
            key={name}
            onClick={() => setTab(name)}
            role="tab"
            type="button"
          >
            {name}
          </button>
        ))}
      </div>
      {candidate?.error && <p className="inline-error">{candidate.error}</p>}
      {files.length === 0 ? (
        <p className="conversion-detail-empty">No converted files are available for this candidate.</p>
      ) : (
        <div className="conversion-files">
          {files.map((file, index) => {
            const finalFile = matchingFinalFile(file, files, detail.final.files, index);
            const hasNoMatchingFinal = tab !== "Final" && !finalFile;
            const lines =
              tab === "Final" || !finalFile
                ? file.content.split("\n").map((text) => ({ kind: "same" as const, text }))
                : lineDiff(finalFile.content, file.content);
            return (
              <article className="conversion-file" key={`${tab}:${file.rel_path}`}>
                <header className="conversion-file-heading">
                  <strong title={file.rel_path}>{file.rel_path}</strong>
                  {hasNoMatchingFinal && (
                    <small className="conversion-new-file-label">
                      New file (no matching final file)
                    </small>
                  )}
                  <button className="button secondary copy-file" onClick={() => void copyFile(file)}>
                    {copied === file.rel_path ? "Copied" : "Copy"}
                  </button>
                </header>
                <pre aria-label={`${file.rel_path} ${tab} diff`} className="conversion-code">
                  {lines.map((line, index) => (
                    <span className={`diff-line diff-${line.kind}`} key={`${index}:${line.kind}`}>
                      <i aria-hidden="true">
                        {line.kind === "added" ? "+" : line.kind === "removed" ? "−" : " "}
                      </i>
                      <code>{line.text || " "}</code>
                    </span>
                  ))}
                </pre>
              </article>
            );
          })}
        </div>
      )}
      {tab === "Final" && (
        <div className="conversion-metadata">
          {detail.final.explanations.length > 0 && (
            <section>
              <h4>Explanations</h4>
              <ul>{detail.final.explanations.map((item, index) => <li key={index}>{item}</li>)}</ul>
            </section>
          )}
          {detail.final.assumptions.length > 0 && (
            <section>
              <h4>Assumptions</h4>
              <ul>{detail.final.assumptions.map((item, index) => <li key={index}>{item}</li>)}</ul>
            </section>
          )}
          {detail.final.unsupported.length > 0 && (
            <section>
              <h4>Unsupported</h4>
              <ul>{detail.final.unsupported.map((item, index) => <li key={index}>{item}</li>)}</ul>
            </section>
          )}
          <section>
            <h4>Findings by check</h4>
            {detail.checks.length === 0 ? (
              <p>No findings.</p>
            ) : (
              detail.checks.map((check) => (
                <div className="conversion-findings" key={check.check}>
                  <strong>{check.check} · {check.status}</strong>
                  {check.findings.map((finding, index) => (
                    <p key={`${finding.rule_id}:${index}`}>
                      {finding.line ? `Line ${finding.line}: ` : ""}
                      {finding.message}
                    </p>
                  ))}
                </div>
              ))
            )}
          </section>
          {detail.devin_sessions.length > 0 && (
            <section>
              <h4>Devin sessions</h4>
              <ul>
                {detail.devin_sessions.map((session, index) => (
                  <li key={`${session.role}:${index}`}>
                    {session.role} · {session.state}
                    {session.url && (
                      <>
                        {" · "}
                        <a href={session.url} rel="noreferrer" target="_blank">Open session</a>
                      </>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>
      )}
    </section>
  );
}
