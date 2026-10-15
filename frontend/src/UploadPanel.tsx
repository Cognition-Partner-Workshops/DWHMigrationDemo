import { useCallback, useEffect, useRef, useState } from "react";
import type { ChangeEvent, DragEvent } from "react";

import {
  deleteUpload,
  getUploads,
  setUploadSelected,
  uploadFiles,
  watchDiscoveryStatus,
  type DiscoveryStatus,
  type UploadRecord,
} from "./api";

const MAX_FILE_BYTES = 100 * 1024 * 1024;
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

type LocalFile = {
  id: string;
  file: File;
  message: string;
};

function extensionOf(fileName: string): string {
  const index = fileName.lastIndexOf(".");
  return index < 0 ? "" : fileName.slice(index).toLowerCase();
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function statusClass(status: string): string {
  return status.toLowerCase().replaceAll("_", "-").replaceAll(" ", "-");
}

export function UploadPanel({
  confirmed,
  uploads,
  onUploadsChange,
  onInventoryChanged,
}: {
  confirmed: boolean;
  uploads: UploadRecord[];
  onUploadsChange: (uploads: UploadRecord[]) => void;
  onInventoryChanged: () => void;
}) {
  const [selectedFiles, setSelectedFiles] = useState<LocalFile[]>([]);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [discovery, setDiscovery] = useState<DiscoveryStatus | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const dropRef = useRef<HTMLDivElement>(null);
  const stopPollingRef = useRef<(() => void) | null>(null);

  const applySnapshot = useCallback(
    (nextUploads: UploadRecord[]) => {
      onUploadsChange(nextUploads);
      onInventoryChanged();
    },
    [onInventoryChanged, onUploadsChange],
  );

  const startPolling = useCallback(() => {
    stopPollingRef.current?.();
    stopPollingRef.current = watchDiscoveryStatus(
      (snapshot) => {
        setDiscovery(snapshot.status);
        if (snapshot.status.state === "done") setNotice("Discovery complete.");
        if (snapshot.status.state === "failed") setNotice("Discovery failed.");
        applySnapshot(snapshot.uploads);
      },
      (pollError) => {
        setError(pollError instanceof Error ? pollError.message : "Discovery status unavailable.");
      },
    );
  }, [applySnapshot]);

  useEffect(() => {
    if (!confirmed) {
      onUploadsChange([]);
      setDiscovery(null);
      stopPollingRef.current?.();
      return;
    }
    let active = true;
    void getUploads()
      .then((rows) => {
        if (!active) return;
        onUploadsChange(rows);
        if (rows.some((row) => ["pending", "queued", "parsing"].includes(row.status))) {
          startPolling();
        }
      })
      .catch((loadError: unknown) => {
        if (active) {
          setError(loadError instanceof Error ? loadError.message : "Could not load uploads.");
        }
      });
    return () => {
      active = false;
      stopPollingRef.current?.();
    };
  }, [confirmed, onUploadsChange, startPolling]);

  function addFiles(fileList: FileList | File[]) {
    const incoming = Array.from(fileList);
    setSelectedFiles((current) => {
      const seen = new Set(current.map(({ file }) => `${file.name}:${file.size}`));
      const additions = incoming
        .filter((file) => !seen.has(`${file.name}:${file.size}`))
        .map((file): LocalFile => {
          const id = `${file.name}-${file.size}-${file.lastModified}`;
          const message =
            file.size > MAX_FILE_BYTES
              ? "File exceeds the 100 MB limit."
              : !ALLOWED_EXTENSIONS.has(extensionOf(file.name))
                ? "Unsupported file format."
                : "";
          return { id, file, message };
        });
      return [...current, ...additions];
    });
    setError("");
    setNotice("");
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    if (event.target.files) {
      addFiles(event.target.files);
      event.target.value = "";
    }
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    if (event.dataTransfer.files.length > 0) {
      addFiles(event.dataTransfer.files);
    }
  }

  async function handleUpload() {
    const validFiles = selectedFiles
      .filter(({ file, message }) => !message && file.size <= MAX_FILE_BYTES)
      .map(({ file }) => file);
    if (!confirmed || validFiles.length === 0 || uploading) return;
    setUploading(true);
    setProgress(0);
    setError("");
    setNotice("");
    try {
      await uploadFiles(validFiles, (loaded, total) => {
        setProgress(total > 0 ? Math.round((loaded / total) * 100) : 0);
      });
      const rows = await getUploads();
      onUploadsChange(rows);
      onInventoryChanged();
      setSelectedFiles((current) =>
        current.filter(({ file }) => !validFiles.includes(file)),
      );
      setNotice("Files uploaded. Discovery is processing the selected files.");
      startPolling();
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "Upload failed.");
    } finally {
      setUploading(false);
      setProgress(null);
    }
  }

  async function handleSelected(row: UploadRecord, selected: boolean) {
    try {
      const updated = await setUploadSelected(row.id, selected);
      onUploadsChange(uploads.map((item) => (item.id === row.id ? updated : item)));
      onInventoryChanged();
    } catch (updateError) {
      setError(updateError instanceof Error ? updateError.message : "Could not update selection.");
    }
  }

  async function handleDelete(row: UploadRecord) {
    if (!window.confirm(`Delete ${row.rel_path}?`)) return;
    try {
      await deleteUpload(row.id);
      onUploadsChange(uploads.filter((item) => item.id !== row.id));
      onInventoryChanged();
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Could not delete upload.");
    }
  }

  const uploadableCount = selectedFiles.filter(({ file, message }) => {
    return !message && file.size <= MAX_FILE_BYTES && ALLOWED_EXTENSIONS.has(extensionOf(file.name));
  }).length;
  const uploadsFinished =
    uploads.length > 0 &&
    uploads.every((row) => ["done", "partial", "unparsed", "rejected"].includes(row.status));
  const noticeMessage =
    discovery?.state === "failed"
      ? "Discovery failed."
      : discovery?.state === "done" || uploadsFinished
        ? "Discovery complete."
        : notice;

  return (
    <div className="upload-workspace">
      <div
        className="drop-zone discovery-drop-zone"
        onDragOver={(event) => event.preventDefault()}
        onDrop={handleDrop}
        ref={dropRef}
      >
        <input
          accept={[...ALLOWED_EXTENSIONS].join(",")}
          aria-label="Choose files to upload"
          className="visually-hidden"
          multiple
          onChange={handleFileChange}
          ref={inputRef}
          type="file"
        />
        <span aria-hidden="true" className="upload-cloud">⇧</span>
        <strong>Drag and drop files here</strong>
        <span>or browse to select multiple files</span>
        <button
          className="browse-button"
          onClick={() => inputRef.current?.click()}
          type="button"
        >
          Browse files
        </button>
        <small>Supported SQL, DDL, ETL exports, Python, shell, YAML and ZIP · Max 100 MB</small>
      </div>
      <div className="discovery-upload-controls">
        <button
          className="button primary upload-button"
          disabled={!confirmed || !uploadableCount || uploading}
          onClick={() => void handleUpload()}
          type="button"
        >
          {uploading ? "Uploading…" : "Upload Files"}
        </button>
        {!confirmed && <span>Confirm a source and target before uploading.</span>}
        {uploading && (
          <div aria-label={`Upload progress ${progress ?? 0}%`} className="upload-progress">
            <progress max="100" value={progress ?? 0} />
            <span>{progress ?? 0}%</span>
          </div>
        )}
      </div>
      {discovery && (
        <div aria-live="polite" className="discovery-progress">
          <span className={`status-dot ${discovery.state === "running" ? "" : "muted-dot"}`} />
          <strong>Discovery {discovery.state}</strong>
          {discovery.files_total > 0 && (
            <span>{discovery.files_completed} / {discovery.files_total} files</span>
          )}
          {discovery.current_file && <span className="current-discovery-file">{discovery.current_file}</span>}
        </div>
      )}
      {(error || noticeMessage) && (
        <p className={error ? "inline-notice error-banner" : "inline-notice"} role={error ? "alert" : "status"}>
          {error || noticeMessage}
        </p>
      )}
      <div className="table-scroll inventory-scroll discovery-upload-table-scroll">
        <table>
          <thead>
            <tr>
              <th scope="col">Select</th>
              <th scope="col">Name</th>
              <th scope="col">Detected type</th>
              <th scope="col">Size</th>
              <th scope="col">Status</th>
              <th scope="col">Objects</th>
              <th scope="col">Issues</th>
              <th scope="col">Action</th>
            </tr>
          </thead>
          <tbody>
            {selectedFiles.map(({ id, file, message }) => (
              <tr key={id}>
                <td>—</td>
                <td className="file-name">{file.name}</td>
                <td>—</td>
                <td>{formatSize(file.size)}</td>
                <td>
                  <span className={`file-status ${message ? "rejected" : "pending"}`}>
                    {message ? "Rejected" : "Selected"}
                  </span>
                  {message && <small className="upload-row-message">{message}</small>}
                </td>
                <td>—</td>
                <td>{message ? 1 : 0}</td>
                <td>
                  <button
                    aria-label={`Remove ${file.name}`}
                    className="row-action"
                    onClick={() => setSelectedFiles((current) => current.filter((row) => row.id !== id))}
                    type="button"
                  >
                    ×
                  </button>
                </td>
              </tr>
            ))}
            {uploads.map((row) => (
              <tr key={row.id}>
                <td>
                  <input
                    aria-label={`Select ${row.rel_path}`}
                    checked={row.selected}
                    onChange={(event) => void handleSelected(row, event.target.checked)}
                    type="checkbox"
                  />
                </td>
                <td className="file-name">{row.rel_path}</td>
                <td>{row.detected_type}</td>
                <td>{formatSize(row.size)}</td>
                <td>
                  <span className={`file-status ${statusClass(row.status)}`}>{row.status}</span>
                  {row.message && <small className="upload-row-message">{row.message}</small>}
                </td>
                <td>{row.object_count}</td>
                <td>{row.issue_count ?? (row.message ? 1 : 0)}</td>
                <td>
                  <button
                    aria-label={`Delete ${row.rel_path}`}
                    className="row-action"
                    onClick={() => void handleDelete(row)}
                    type="button"
                  >
                    ×
                  </button>
                </td>
              </tr>
            ))}
            {selectedFiles.length === 0 && uploads.length === 0 && (
              <tr>
                <td className="empty-table" colSpan={8}>No files uploaded yet.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
