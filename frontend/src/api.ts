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

export type ResourceItem = {
  id: string;
  name: string;
  description: string;
  object_types: string[];
  extraction_hint: string;
  tier: "mandatory" | "optional";
  available: boolean;
  count: number | null;
  status: string;
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
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    if (payload?.detail) {
      message = payload.detail;
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
