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
