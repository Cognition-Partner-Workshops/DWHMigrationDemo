export type Platform = {
  id: string;
  name: string;
};

export type PlatformResponse = {
  sources: Platform[];
  targets: Platform[];
};

export type HealthResponse = {
  status: string;
  mode: string;
  version: string;
  devin_configured: boolean;
  conversion_configured: boolean;
};

export type SettingsResponse = {
  devin_pat: {
    configured: boolean;
    masked: string;
  };
  devin_org_id: string;
  token_kind: "v3" | "legacy" | "none";
  devin_api_base_url: string;
  confidence_threshold: number;
  max_concurrent_sessions: number;
  acu_cap_per_run: number | null;
};

export type SettingsUpdate = {
  devin_pat?: string;
  devin_org_id?: string;
  devin_api_base_url?: string;
  confidence_threshold?: number;
  max_concurrent_sessions?: number;
  acu_cap_per_run?: number | null;
};

export type PlanCitation = {
  kb_id?: number;
  url?: string;
  note: string;
  unverified?: boolean;
};

export type PlanSection = {
  id: string;
  title: string;
  content: string;
  citations: PlanCitation[];
  platform_level_estimate: boolean;
  manual_review?: boolean;
  unverified_citations?: boolean;
};

export type PlanDocument = {
  source: string;
  target: string;
  overall_confidence: number | null;
  sections: PlanSection[];
};

export type PlanStatus = {
  state: string;
  progress: {
    phase: string;
    elapsed_s: number;
    devin_url: string | null;
  };
  plan: PlanDocument | null;
  origin: "devin" | "local_draft" | null;
  reason: string | null;
  cached: boolean;
  kb_version: number | null;
  stale: boolean;
  inventory_stale: boolean;
  label?: string | null;
  created_at?: string | null;
  devin_session_id?: string | null;
};

export type SessionResponse = {
  session_id: string;
  source: string;
  target: string;
  plan: PlanStatus;
};

export type SessionView = {
  source: string;
  target: string;
  plan: PlanStatus | null;
};

export type CurrentSessionResponse = {
  source: string | null;
  target: string | null;
  confirmed: boolean;
};

export type ResourceItem = {
  id: string;
  name: string;
  description: string;
  object_types: string[];
  extraction_hint: string;
  tier: "mandatory" | "optional";
  available: boolean;
  count: number | null;
  auto_available: boolean;
  status: string;
};

export type UploadRecord = {
  id: string;
  rel_path: string;
  size: number;
  detected_type: string;
  status: string;
  message: string | null;
  selected: boolean;
  object_count: number;
  issue_count?: number;
};

export type DiscoveryStatus = {
  state: string;
  files_total: number;
  files_completed: number;
  current_file: string | null;
};

export type DiscoverySnapshot = {
  status: DiscoveryStatus;
  uploads: UploadRecord[];
};

export type InventoryObject = {
  id: string;
  upload_id: string | null;
  file: string;
  type: string;
  schema_name: string | null;
  name: string;
  qualified_name: string;
  status: string;
  start_line: number;
  end_line: number;
  source_preview: string;
  external: boolean | number;
  upstream_count?: number;
  downstream_count?: number;
};

export type InventoryIssue = {
  severity: string;
  code: string;
  message: string;
};

export type InventoryNeighbor = {
  id: string;
  type: string;
  qualified_name: string;
  kind: string;
  external: boolean | number;
};

export type InventoryDetail = InventoryObject & {
  issues: InventoryIssue[];
  upstream: InventoryNeighbor[];
  downstream: InventoryNeighbor[];
};

export type InventoryPage = {
  items: InventoryObject[];
  page: number;
  page_size: number;
  total: number;
  external_total: number;
};

export type GraphEdge = {
  source_id: string;
  target_id: string;
  kind: string;
  external: boolean | number;
  source_name?: string;
  target_name?: string;
};

export type GraphResult = {
  nodes: InventoryObject[];
  edges: GraphEdge[];
  truncated: boolean;
};

export type InventoryQuery = {
  q?: string;
  type?: string;
  status?: string;
  file?: string;
  page?: number;
  page_size?: number;
};

export type ConvertEstimate = {
  objects: number;
  batches: number;
  oversized_batches: number;
  voter_sessions: number;
  max_escalation_sessions: number;
  max_sessions: number;
  batch_size: number;
  session_timeout_minutes: number;
  max_concurrent_sessions: number;
  devin_configured: boolean;
  devin_host: string;
  acu_cap: null;
};

export type ConversionBatch = {
  index: number;
  status: string;
  objects: string[];
  voters: Record<string, string>;
  escalated: boolean;
  object_count: number;
  counts: Record<string, number>;
};

export type ConversionJobStatus = {
  run_id: string;
  state: string;
  progress: { elapsed_s: number; batches: Record<string, Omit<ConversionBatch, "index" | "counts" | "object_count">> };
  batches: ConversionBatch[];
  counts: Record<string, number>;
  object_count: number;
  batch_count: number;
  reason: string | null;
};

export type ConversionRow = {
  object_id: string;
  qualified_name: string;
  type: string;
  status: "converted" | "expert_review" | "failed" | "cancelled" | "pending";
  confidence: number | null;
  agreement: number | null;
  pass_rate: number | null;
  escalated: boolean;
  checks: Record<string, "passed" | "failed" | "not_applicable">;
};

export type ConversionFile = {
  rel_path: string;
  language: string;
  content: string;
  etl_format_id?: string | null;
};

export type ConversionCheck = {
  check: string;
  status: "passed" | "failed" | "not_applicable";
  findings: Array<{
    check: string;
    rule_id: string;
    severity: "error" | "warning";
    message: string;
    line: number | null;
  }>;
};

export type ConversionCandidate = {
  files: ConversionFile[];
  self_confidence: number | null;
  state: string;
  error: string | null;
};

export type ConversionDetailResponse = {
  final: {
    files: ConversionFile[];
    explanations: string[];
    assumptions: string[];
    unsupported: string[];
  };
  picked_voter: string | null;
  candidates: Record<"A" | "B" | "C", ConversionCandidate | null>;
  pairwise: Record<string, number>;
  checks: ConversionCheck[];
  escalation: {
    role: string;
    session_id: string | null;
    url: string | null;
    state: string;
    acus: number | null;
    error: string | null;
  } | null;
  devin_sessions: Array<{
    role: string;
    url: string | null;
    state: string;
    acus: number | null;
  }>;
};

export type KBRefreshProgress = {
  phase: string;
  pages_fetched: number;
  pages_skipped: number;
  pages_failed: number;
  chunks: number;
  elapsed: number;
  running: boolean;
};

export type KBStatusResponse = {
  current_version: number | null;
  created_at: string | null;
  documents: number;
  chunks: number;
  last_refresh_summary: Record<string, unknown> | null;
  running: KBRefreshProgress | null;
  rollback_versions: number[];
  seed_error: number | null;
};

function csrfCookie(): string {
  const entry = document.cookie
    .split(";")
    .map((cookie) => cookie.trim())
    .find((cookie) => cookie.startsWith("migraai_csrf="));
  return entry ? decodeURIComponent(entry.slice("migraai_csrf=".length)) : "";
}

export async function apiRequest<T>(
  path: string,
  options: { method?: string; body?: unknown } = {},
): Promise<T> {
  const method = (options.method ?? "GET").toUpperCase();
  const headers = new Headers({ Accept: "application/json" });
  if (options.body !== undefined) {
    headers.set("Content-Type", "application/json");
  }
  if (["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
    const csrf = csrfCookie();
    if (csrf) {
      headers.set("X-MigraAI-CSRF", csrf);
    }
  }

  const response = await fetch(path, {
    method,
    headers,
    credentials: "same-origin",
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  if (!response.ok) {
    let message = `Request failed (${response.status}).`;
    const payload = (await response.json().catch(() => null)) as
      | { detail?: string; message?: string }
      | null;
    if (payload?.detail || payload?.message) {
      message = payload.detail ?? payload.message ?? message;
    }
    throw new Error(message);
  }
  return (await response.json()) as T;
}

export async function exchangeLaunchToken(token: string): Promise<void> {
  const response = await fetch(`/api/auth/launch?token=${encodeURIComponent(token)}`, {
    credentials: "same-origin",
  });
  if (!response.ok) {
    throw new Error("The launch link is invalid or has already been used.");
  }
}

export function confirmSession(source: string, target: string): Promise<SessionResponse> {
  return apiRequest<SessionResponse>("/api/session/confirm", {
    method: "POST",
    body: { source, target },
  });
}

export function getCurrentSession(): Promise<CurrentSessionResponse> {
  return apiRequest<CurrentSessionResponse>("/api/session");
}

export function getPlan(): Promise<PlanStatus> {
  return apiRequest<PlanStatus>("/api/plan");
}

export function startPlan(regenerate = false): Promise<PlanStatus> {
  return apiRequest<PlanStatus>("/api/plan", {
    method: "POST",
    body: { regenerate },
  });
}

export function cancelPlanJob(): Promise<PlanStatus> {
  return apiRequest<PlanStatus>("/api/plan/job", { method: "DELETE" });
}

export function postConvertEstimate(): Promise<ConvertEstimate> {
  return apiRequest<ConvertEstimate>("/api/convert/estimate", {
    method: "POST",
    body: {},
  });
}

export function startConversion(): Promise<{ job_id: string }> {
  return apiRequest<{ job_id: string }>("/api/convert", {
    method: "POST",
    body: { consent: true },
  });
}

export function getConversionJob(jobId: string): Promise<ConversionJobStatus> {
  return apiRequest<ConversionJobStatus>(`/api/convert/jobs/${encodeURIComponent(jobId)}`);
}

export function cancelConversionJob(jobId: string): Promise<ConversionJobStatus> {
  return apiRequest<ConversionJobStatus>(
    `/api/convert/jobs/${encodeURIComponent(jobId)}/cancel`,
    { method: "POST", body: {} },
  );
}

export function getConversions(filters: {
  status?: string;
  type?: string;
  failed_check?: string;
  search?: string;
  page?: number;
  page_size?: number;
} = {}): Promise<{ items: ConversionRow[]; page: number; page_size: number; total: number }> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== "") {
      params.set(key, String(value));
    }
  }
  return apiRequest<{ items: ConversionRow[]; page: number; page_size: number; total: number }>(
    `/api/conversions?${params.toString()}`,
  );
}

export function getConversionDetail(objectId: string): Promise<ConversionDetailResponse> {
  return apiRequest<ConversionDetailResponse>(`/api/conversions/${encodeURIComponent(objectId)}`);
}

export function getResources(): Promise<ResourceItem[]> {
  return apiRequest<ResourceItem[]>("/api/resources");
}

export function updateResourceAvailability(
  resourceId: string,
  available: boolean,
): Promise<{ id: string; available: boolean }> {
  return apiRequest<{ id: string; available: boolean }>(
    `/api/resources/${encodeURIComponent(resourceId)}`,
    { method: "PATCH", body: { available } },
  );
}

function uploadError(xhr: XMLHttpRequest): Error {
  try {
    const payload = JSON.parse(xhr.responseText) as { detail?: string };
    if (payload.detail) {
      return new Error(payload.detail);
    }
  } catch {
    // The server may return a non-JSON error body.
  }
  return new Error(`Upload failed (${xhr.status}).`);
}

export function uploadFiles(
  files: File[],
  onProgress: (loaded: number, total: number) => void,
): Promise<{ uploads: UploadRecord[]; replaced: string[] }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/uploads");
    xhr.withCredentials = true;
    xhr.setRequestHeader("Accept", "application/json");
    const csrf = csrfCookie();
    if (csrf) {
      xhr.setRequestHeader("X-MigraAI-CSRF", csrf);
    }
    xhr.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable) {
        onProgress(event.loaded, event.total);
      }
    });
    xhr.addEventListener("error", () => reject(new Error("Upload network error.")));
    xhr.addEventListener("abort", () => reject(new Error("Upload was cancelled.")));
    xhr.addEventListener("load", () => {
      if (xhr.status < 200 || xhr.status >= 300) {
        reject(uploadError(xhr));
        return;
      }
      try {
        const payload = JSON.parse(xhr.responseText) as {
          uploads: UploadRecord[];
          replaced: string[];
        };
        resolve(payload);
      } catch {
        reject(new Error("The upload response could not be read."));
      }
    });
    const formData = new FormData();
    for (const file of files) {
      formData.append("files", file, file.name);
    }
    xhr.send(formData);
  });
}

export function getUploads(): Promise<UploadRecord[]> {
  return apiRequest<UploadRecord[]>("/api/uploads");
}

export function setUploadSelected(uploadId: string, selected: boolean): Promise<UploadRecord> {
  return apiRequest<UploadRecord>(`/api/uploads/${encodeURIComponent(uploadId)}`, {
    method: "PATCH",
    body: { selected },
  });
}

export function deleteUpload(uploadId: string): Promise<{ deleted: boolean }> {
  return apiRequest<{ deleted: boolean }>(`/api/uploads/${encodeURIComponent(uploadId)}`, {
    method: "DELETE",
  });
}

export function getDiscoveryStatus(): Promise<DiscoveryStatus> {
  return apiRequest<DiscoveryStatus>("/api/discovery/status");
}

export function getInventory(query: InventoryQuery = {}): Promise<InventoryPage> {
  const params = new URLSearchParams();
  if (query.q) params.set("q", query.q);
  if (query.type) params.set("type", query.type);
  if (query.status) params.set("status", query.status);
  if (query.file) params.set("file", query.file);
  params.set("page", String(query.page ?? 1));
  params.set("page_size", String(query.page_size ?? 50));
  return apiRequest<InventoryPage>(`/api/inventory?${params.toString()}`);
}

export function getInventoryDetail(objectId: string): Promise<InventoryDetail> {
  return apiRequest<InventoryDetail>(`/api/inventory/${encodeURIComponent(objectId)}`);
}

export function getGraph(objectId: string, depth: number): Promise<GraphResult> {
  const params = new URLSearchParams({
    object_id: objectId,
    depth: String(depth),
  });
  return apiRequest<GraphResult>(`/api/graph?${params.toString()}`);
}

export function watchDiscoveryStatus(
  onUpdate: (snapshot: DiscoverySnapshot) => void,
  onError: (error: unknown) => void,
): () => void {
  let stopped = false;
  let timer: number | undefined;
  const stop = () => {
    stopped = true;
    if (timer !== undefined) {
      window.clearTimeout(timer);
    }
  };
  const poll = async () => {
    try {
      const [status, uploads] = await Promise.all([getDiscoveryStatus(), getUploads()]);
      if (stopped) {
        return;
      }
      onUpdate({ status, uploads });
      if (["idle", "done", "failed"].includes(status.state)) {
        stop();
        return;
      }
    } catch (error) {
      if (!stopped) {
        onError(error);
        stop();
      }
      return;
    }
    if (!stopped) {
      timer = window.setTimeout(() => void poll(), 1000);
    }
  };
  void poll();
  return stop;
}

export function getKBStatus(): Promise<KBStatusResponse> {
  return apiRequest<KBStatusResponse>("/api/kb/status");
}

export function startKBRefresh(): Promise<{ status: string }> {
  return apiRequest<{ status: string }>("/api/kb/refresh", { method: "POST", body: {} });
}

export function cancelKBRefresh(): Promise<{ status: string }> {
  return apiRequest<{ status: string }>("/api/kb/refresh", { method: "DELETE" });
}

export function rollbackKB(): Promise<{ current_version: number }> {
  return apiRequest<{ current_version: number }>("/api/kb/rollback", {
    method: "POST",
    body: {},
  });
}
