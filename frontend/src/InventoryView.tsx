import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";

import {
  getInventory,
  getInventoryDetail,
  type InventoryDetail,
  type InventoryObject,
  type InventoryQuery,
  type UploadRecord,
} from "./api";
const GraphView = lazy(() => import("./GraphView").then(({ GraphView }) => ({ default: GraphView })));

type SortKey = "qualified_name" | "type" | "status";
const COMMON_OBJECT_TYPES = [
  "database",
  "function",
  "index",
  "materialized_view",
  "package",
  "procedure",
  "schema",
  "sequence",
  "table",
  "trigger",
  "type",
  "view",
];

function sortRows(rows: InventoryObject[], key: SortKey, direction: "asc" | "desc") {
  return [...rows].sort((left, right) => {
    const compared = left[key].localeCompare(right[key], undefined, { sensitivity: "base" });
    return direction === "asc" ? compared : -compared;
  });
}

export function InventoryView({
  revision,
  uploads,
}: {
  revision: number;
  uploads: UploadRecord[];
}) {
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [type, setType] = useState("");
  const [status, setStatus] = useState("");
  const [file, setFile] = useState("");
  const [page, setPage] = useState(1);
  const [items, setItems] = useState<InventoryObject[]>([]);
  const [total, setTotal] = useState(0);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<InventoryDetail | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("qualified_name");
  const [sortDirection, setSortDirection] = useState<"asc" | "desc">("asc");
  const [showGraph, setShowGraph] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedSearch(search.trim());
      setPage(1);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    const query: InventoryQuery = {
      q: debouncedSearch,
      type,
      status,
      file,
      page,
      page_size: 50,
    };
    void getInventory(query)
      .then((result) => {
        if (!active) return;
        setItems(result.items);
        setTotal(result.total);
        setError("");
      })
      .catch((loadError: unknown) => {
        if (active) setError(loadError instanceof Error ? loadError.message : "Inventory unavailable.");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [debouncedSearch, file, page, revision, status, type]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let active = true;
    void getInventoryDetail(selectedId)
      .then((result) => {
        if (active) setDetail(result);
      })
      .catch((loadError: unknown) => {
        if (active) {
          setDetail(null);
          setError(loadError instanceof Error ? loadError.message : "Object detail unavailable.");
        }
      });
    return () => {
      active = false;
    };
  }, [revision, selectedId]);

  const typeOptions = useMemo(
    () => [...new Set([...COMMON_OBJECT_TYPES, ...items.map((item) => item.type)])].sort(),
    [items],
  );
  const visibleItems = useMemo(
    () => sortRows(items, sortKey, sortDirection),
    [items, sortDirection, sortKey],
  );
  const pages = Math.max(1, Math.ceil(total / 50));

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDirection((direction) => (direction === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDirection("asc");
    }
  }

  const selectObject = useCallback((objectId: string) => {
    setSelectedId(objectId);
    setShowGraph(false);
  }, []);

  return (
    <div className="inventory-view">
      <div className="inventory-list-pane">
        <div className="inventory-toolbar">
          <label>
            <span className="visually-hidden">Search inventory</span>
            <input
              aria-label="Search inventory"
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search object names…"
              type="search"
              value={search}
            />
          </label>
          <label>
            <span className="visually-hidden">Filter by type</span>
            <select aria-label="Filter by type" onChange={(event) => { setType(event.target.value); setPage(1); }} value={type}>
              <option value="">All types</option>
              {typeOptions.map((option) => <option key={option} value={option}>{option}</option>)}
            </select>
          </label>
          <label>
            <span className="visually-hidden">Filter by status</span>
            <select aria-label="Filter by status" onChange={(event) => { setStatus(event.target.value); setPage(1); }} value={status}>
              <option value="">All statuses</option>
              {["parsed", "partial", "unparsed", "external"].map((option) => (
                <option key={option} value={option}>{option}</option>
              ))}
            </select>
          </label>
          <label>
            <span className="visually-hidden">Filter by file</span>
            <select aria-label="Filter by file" onChange={(event) => { setFile(event.target.value); setPage(1); }} value={file}>
              <option value="">All files</option>
              {uploads.map((upload) => (
                <option key={upload.id} value={upload.rel_path}>{upload.rel_path}</option>
              ))}
            </select>
          </label>
        </div>
        {error && <p className="inventory-error" role="alert">{error}</p>}
        <div className="inventory-results-scroll">
          <table className="inventory-results">
            <thead>
              <tr>
                <th><button onClick={() => toggleSort("qualified_name")} type="button">Name {sortKey === "qualified_name" ? (sortDirection === "asc" ? "↑" : "↓") : ""}</button></th>
                <th><button onClick={() => toggleSort("type")} type="button">Type {sortKey === "type" ? (sortDirection === "asc" ? "↑" : "↓") : ""}</button></th>
                <th>File:lines</th>
                <th><button onClick={() => toggleSort("status")} type="button">Status {sortKey === "status" ? (sortDirection === "asc" ? "↑" : "↓") : ""}</button></th>
                <th>↑</th>
                <th>↓</th>
              </tr>
            </thead>
            <tbody>
              {visibleItems.map((item) => (
                <tr
                  aria-selected={selectedId === item.id}
                  className="inventory-row"
                  key={item.id}
                  onClick={() => selectObject(item.id)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") selectObject(item.id);
                  }}
                  role="button"
                  tabIndex={0}
                >
                  <td>{item.qualified_name}</td>
                  <td>{item.type}</td>
                  <td>{item.file}:{item.start_line}-{item.end_line}</td>
                  <td><span className={`file-status ${item.status}`}>{item.status}</span></td>
                  <td>{item.upstream_count ?? 0}</td>
                  <td>{item.downstream_count ?? 0}</td>
                </tr>
              ))}
              {!loading && visibleItems.length === 0 && (
                <tr><td className="empty-table" colSpan={6}>No objects match these filters.</td></tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="inventory-pagination">
          <span>{loading ? "Loading…" : `${total} objects`}</span>
          <button disabled={page <= 1} onClick={() => setPage((current) => current - 1)} type="button">Previous</button>
          <span>Page {page} of {pages}</span>
          <button disabled={page >= pages} onClick={() => setPage((current) => current + 1)} type="button">Next</button>
        </div>
      </div>

      <aside aria-label="Object details" className="inventory-detail-pane">
        {detail ? (
          <>
            <div className="inventory-detail-heading">
              <div>
                <span className="eyebrow">{detail.type}</span>
                <h3>{detail.qualified_name}</h3>
                <small>{detail.file}:{detail.start_line}-{detail.end_line}</small>
              </div>
              <button className="button graph-button" onClick={() => setShowGraph((value) => !value)} type="button">
                {showGraph ? "Hide graph" : "Show graph"}
              </button>
            </div>
            {showGraph ? (
              <Suspense fallback={<p className="detail-empty">Loading dependency graph…</p>}>
                <GraphView
                  onSelectObject={selectObject}
                  objectId={detail.id}
                />
              </Suspense>
            ) : (
              <div className="inventory-detail-scroll">
                <h4>Source preview</h4>
                <pre className="source-preview">{detail.source_preview}</pre>
                <h4>Issues</h4>
                {detail.issues.length ? (
                  <ul className="inventory-issues">
                    {detail.issues.map((issue, index) => (
                      <li key={`${issue.code}-${index}`}>
                        <strong>{issue.severity}</strong> · {issue.message}
                      </li>
                    ))}
                  </ul>
                ) : <p className="detail-empty">No issues reported.</p>}
                <h4>Upstream ({detail.upstream.length})</h4>
                <EdgeList edges={detail.upstream} onSelect={selectObject} />
                <h4>Downstream ({detail.downstream.length})</h4>
                <EdgeList edges={detail.downstream} onSelect={selectObject} />
              </div>
            )}
          </>
        ) : (
          <p className="detail-empty">Select an object to inspect its source and dependencies.</p>
        )}
      </aside>
    </div>
  );
}

function EdgeList({
  edges,
  onSelect,
}: {
  edges: InventoryDetail["upstream"];
  onSelect: (id: string) => void;
}) {
  if (edges.length === 0) {
    return <p className="detail-empty">No related objects.</p>;
  }
  return (
    <ul className="inventory-edge-list">
      {edges.map((edge, index) => (
        <li key={`${edge.id}-${edge.kind}-${index}`}>
          <button disabled={!edge.id} onClick={() => edge.id && onSelect(edge.id)} type="button">
            {edge.qualified_name}
          </button>
          <span>{edge.kind}</span>
        </li>
      ))}
    </ul>
  );
}
