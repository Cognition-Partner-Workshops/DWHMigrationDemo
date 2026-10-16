import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";

const settingsResponse = {
  devin_pat: { configured: true, masked: "••••abcd" },
  devin_org_id: "org-demo",
  token_kind: "v3",
  devin_api_base_url: "https://api.devin.ai",
  confidence_threshold: 0.85,
  max_concurrent_sessions: 4,
  acu_cap_per_run: null,
};

const fallbackPlan = {
  state: "failed_fallback",
  progress: { phase: "failed_fallback", elapsed_s: 0, devin_url: null },
  plan: null,
  origin: "local_draft",
  reason: "Devin token not configured",
  cached: false,
  kb_version: 1,
  stale: false,
  inventory_stale: false,
  created_at: "2026-10-15T00:00:00Z",
};

function mockApi(
  devinConfigured = true,
  currentSession: {
    source: string | null;
    target: string | null;
    confirmed: boolean;
  } = { source: null, target: null, confirmed: false },
  planResponse: Record<string, unknown> = fallbackPlan,
  conversionConfigured = devinConfigured,
  conversionObjectCount = 0,
) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/api/health")) {
      return Response.json({
        status: "ready",
        mode: "local",
        version: "0.1.0",
        devin_configured: devinConfigured,
        conversion_configured: conversionConfigured,
      });
    }
    if (path.endsWith("/api/platforms")) {
      return Response.json({
        sources: [
          { id: "teradata", name: "Teradata" },
          { id: "snowflake", name: "Snowflake" },
        ],
        targets: [
          { id: "databricks", name: "Databricks" },
          { id: "snowflake", name: "Snowflake" },
        ],
      });
    }
    if (path.endsWith("/api/settings")) {
      return Response.json(settingsResponse);
    }
    if (path.endsWith("/api/convert/estimate")) {
      return Response.json({
        objects: conversionObjectCount,
        batches: conversionObjectCount > 0 ? 3 : 0,
        oversized_batches: 0,
        voter_sessions: conversionObjectCount > 0 ? 9 : 0,
        max_escalation_sessions: conversionObjectCount > 0 ? 3 : 0,
        max_sessions: conversionObjectCount > 0 ? 12 : 0,
        batch_size: 20,
        session_timeout_minutes: 45,
        max_concurrent_sessions: 4,
        devin_configured: conversionConfigured,
        devin_host: conversionConfigured ? "local mock" : "Devin",
        acu_cap: null,
      });
    }
    if (path.endsWith("/api/session") && !path.endsWith("/api/session/confirm")) {
      return Response.json(currentSession);
    }
    if (path.endsWith("/api/plan")) {
      return Response.json(planResponse);
    }
    if (path.endsWith("/api/resources")) {
      return Response.json([]);
    }
    if (path.endsWith("/api/uploads")) {
      return Response.json([]);
    }
    if (path.endsWith("/api/discovery/status")) {
      return Response.json({
        state: "idle",
        files_total: 0,
        files_completed: 0,
        current_file: null,
      });
    }
    if (path.startsWith("/api/inventory?")) {
      return Response.json({ items: [], page: 1, page_size: 50, total: 0 });
    }
    if (path.endsWith("/api/kb/status")) {
      return Response.json({
        current_version: 1,
        created_at: "2026-10-15T00:00:00Z",
        documents: 0,
        chunks: 0,
        last_refresh_summary: null,
        running: null,
        rollback_versions: [],
        seed_error: null,
      });
    }
    if (path.endsWith("/api/session/confirm")) {
      const pair = JSON.parse(String(init?.body ?? "{}")) as {
        source: string;
        target: string;
      };
      return Response.json({
        session_id: "session-1",
        ...pair,
        plan: fallbackPlan,
      });
    }
    return Response.json(settingsResponse);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function confirmSystems(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByRole("option", { name: "Teradata" });
  await screen.findByRole("option", { name: "Databricks" });
  await user.selectOptions(await screen.findByLabelText("Source platform"), "teradata");
  await user.selectOptions(screen.getByLabelText("Target platform"), "databricks");
  await user.click(screen.getByRole("button", { name: /confirm/i }));
}

function mockPlanningScroll() {
  const main = screen.getByRole("main");
  const planningSection = screen.getByRole("region", { name: "Migration Planning" });
  Object.defineProperty(main, "offsetTop", { configurable: true, value: 58 });
  Object.defineProperty(planningSection, "offsetTop", { configurable: true, value: 558 });
  const scrollTo = vi.fn();
  const scrollIntoView = vi.fn();
  main.scrollTo = scrollTo;
  planningSection.scrollIntoView = scrollIntoView;
  return { scrollIntoView, scrollTo };
}

describe("MigraAI foundation UI", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    window.history.replaceState(null, "", "/");
  });

  it("enables Confirm only when source and target are selected and different", async () => {
    const user = userEvent.setup();
    mockApi();
    render(<App />);

    const confirm = await screen.findByRole("button", { name: /confirm/i });
    expect(confirm).toBeDisabled();
    await screen.findByRole("option", { name: "Databricks" });
    await user.selectOptions(screen.getByLabelText("Source platform"), "snowflake");
    await user.selectOptions(screen.getByLabelText("Target platform"), "snowflake");
    expect(confirm).toBeDisabled();
    await user.selectOptions(screen.getByLabelText("Target platform"), "databricks");
    expect(confirm).toBeEnabled();
  });

  it("scrolls only the main container to Planning after Confirm", async () => {
    const user = userEvent.setup();
    mockApi(false);
    render(<App />);
    const { scrollIntoView, scrollTo } = mockPlanningScroll();
    vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: false }));

    await confirmSystems(user);

    await waitFor(() =>
      expect(scrollTo).toHaveBeenCalledWith({ top: 500, behavior: "smooth" }),
    );
    expect(scrollIntoView).not.toHaveBeenCalled();
  });

  it("uses automatic main scrolling when reduced motion is preferred", async () => {
    const user = userEvent.setup();
    mockApi(false);
    render(<App />);
    const { scrollTo } = mockPlanningScroll();
    vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: true }));

    await confirmSystems(user);

    await waitFor(() =>
      expect(scrollTo).toHaveBeenCalledWith({ top: 500, behavior: "auto" }),
    );
  });

  it("restores a confirmed pair and resumes polling a running plan", async () => {
    const runningPlan = {
      ...fallbackPlan,
      state: "running",
      progress: { phase: "running", elapsed_s: 12, devin_url: null },
      origin: "devin",
      reason: null,
    };
    const fetchMock = mockApi(
      true,
      { source: "teradata", target: "snowflake", confirmed: true },
      runningPlan,
    );
    render(<App />);

    const source = await screen.findByLabelText("Source platform");
    const target = await screen.findByLabelText("Target platform");
    const confirm = screen.getByRole("button", { name: /confirm/i });
    expect(source).toHaveValue("teradata");
    expect(target).toHaveValue("snowflake");
    expect(source).toBeEnabled();
    expect(target).toBeEnabled();
    expect(confirm).toBeDisabled();
    expect(await screen.findByText("running")).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([input]) => String(input).endsWith("/api/session/confirm")),
    ).toBe(false);

    await waitFor(
      () =>
        expect(
          fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/api/plan")).length,
        ).toBeGreaterThan(1),
      { timeout: 5000 },
    );
  });

  it("confirms a changed pair after restoring an existing session", async () => {
    const user = userEvent.setup();
    const fetchMock = mockApi(true, {
      source: "teradata",
      target: "databricks",
      confirmed: true,
    });
    render(<App />);

    const source = await screen.findByLabelText("Source platform");
    const confirm = screen.getByRole("button", { name: /confirm/i });
    expect(source).toBeEnabled();
    expect(confirm).toBeDisabled();

    await user.selectOptions(source, "snowflake");
    expect(confirm).toBeEnabled();
    await user.click(confirm);

    await waitFor(() => expect(confirm).toBeDisabled());
    const planPanel = screen.getByRole("region", { name: "Migration Planning" });
    expect(within(planPanel).getByText("Snowflake")).toBeInTheDocument();
    expect(within(planPanel).getByText("Databricks")).toBeInTheDocument();

    const confirmCall = fetchMock.mock.calls.find(
      ([input, init]) =>
        String(input).endsWith("/api/session/confirm") && init?.method === "POST",
    );
    expect(confirmCall).toBeDefined();
    expect(JSON.parse(String(confirmCall?.[1]?.body))).toEqual({
      source: "snowflake",
      target: "databricks",
    });
  });

  it("keeps Upload disabled until confirmation and Convert disabled until a server upload", async () => {
    const user = userEvent.setup();
    mockApi();
    render(<App />);

    const upload = await screen.findByRole("button", { name: /upload files/i });
    const convert = screen.getByRole("button", { name: /convert items/i });
    const download = screen.getByRole("button", { name: /download converted files/i });
    expect(
      screen.getByText("1. Browse or drop files · 2. Upload Files · 3. Convert Items"),
    ).toBeInTheDocument();
    expect(convert).toHaveAttribute("title", "Upload files first");
    expect(download).toHaveAttribute(
      "title",
      "Run a conversion before downloading.",
    );
    expect(upload).toBeDisabled();
    expect(convert).toBeDisabled();
    expect(download).toBeDisabled();

    const file = new File(["select 1"], "tables.sql", { type: "text/plain" });
    fireEvent.change(screen.getByLabelText("Choose files to upload"), {
      target: { files: [file] },
    });
    expect(upload).toBeDisabled();
    await confirmSystems(user);
    expect(upload).toBeEnabled();
    expect(convert).toBeDisabled();
    expect(download).toBeDisabled();
  });

  it("shows the PAT as masked settings metadata, not as an input value", async () => {
    const user = userEvent.setup();
    mockApi();
    render(<App />);

    await user.click(await screen.findByRole("button", { name: /open settings/i }));
    await screen.findByText("Current token: ••••abcd");
    const token = await screen.findByLabelText(/Devin personal access token/);
    expect(token).toHaveAttribute("type", "password");
    expect(token).toHaveValue("");
    expect(token).toHaveAttribute("placeholder", "••••abcd");
    expect(screen.getByText("Current token: ••••abcd")).toBeInTheDocument();
  });

  it("shows local data handling text and the upload cleanup note", async () => {
    mockApi(false);
    const { container } = render(<App />);

    expect(await screen.findByText("Your data stays on this machine")).toBeInTheDocument();
    expect(screen.getByText("Uploads are deleted on exit")).toBeInTheDocument();
    expect(container.querySelector(".egress-banner svg")).toBeInTheDocument();
  });

  it("shows Devin cloud egress when Devin is configured", async () => {
    mockApi(true);
    render(<App />);

    expect(await screen.findByText("Conversions are sent to Devin cloud")).toBeInTheDocument();
  });

  it("opens consent when mock mode configures conversion without a planning key", async () => {
    const user = userEvent.setup();
    mockApi(
      false,
      { source: "teradata", target: "snowflake", confirmed: true },
      fallbackPlan,
      true,
      56,
    );
    render(<App />);

    expect(await screen.findByText("Devin: configured")).toBeInTheDocument();
    expect(
      await screen.findByText("Local mock mode is active; conversion code stays on this machine."),
    ).toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "Convert Items" }));

    expect(await screen.findByRole("dialog")).toHaveTextContent("Send code to Devin?");
  });

  it("sends same-origin credentials and CSRF token on mutations", async () => {
    const fetchMock = mockApi();
    document.cookie = "migraai_csrf=csrf-test; path=/";
    render(<App />);
    await waitFor(() => expect(screen.getByLabelText("Source platform")).toBeInTheDocument());

    const user = userEvent.setup();
    await confirmSystems(user);
    const mutationCall = fetchMock.mock.calls.find(([input]) =>
      String(input).endsWith("/api/session/confirm"),
    );
    expect(mutationCall).toBeDefined();
    const options = mutationCall?.[1];
    expect(options).toMatchObject({
      method: "POST",
      credentials: "same-origin",
    });
    expect(new Headers(options?.headers).get("X-MigraAI-CSRF")).toBe("csrf-test");
  });
});
