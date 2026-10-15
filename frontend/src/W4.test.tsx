import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SettingsDialog } from "./App";
import { KnowledgeBaseSettings } from "./KnowledgeBaseSettings";
import { PlanningPanel } from "./PlanningPanel";
import type { KBStatusResponse, PlanDocument, PlanStatus, SessionResponse, SettingsResponse } from "./api";

const apiMocks = vi.hoisted(() => ({
  apiRequest: vi.fn(),
  exchangeLaunchToken: vi.fn(),
  confirmSession: vi.fn(),
  getPlan: vi.fn(),
  startPlan: vi.fn(),
  cancelPlanJob: vi.fn(),
  getResources: vi.fn(),
  updateResourceAvailability: vi.fn(),
  getKBStatus: vi.fn(),
  startKBRefresh: vi.fn(),
  cancelKBRefresh: vi.fn(),
  rollbackKB: vi.fn(),
}));

vi.mock("./api", () => ({ ...apiMocks }));

const settings: SettingsResponse = {
  devin_pat: { configured: false, masked: "" },
  devin_org_id: "",
  token_kind: "none",
  devin_api_base_url: "https://api.devin.ai",
  confidence_threshold: 0.85,
  max_concurrent_sessions: 4,
  acu_cap_per_run: null,
};

function makeSection(id: string, title: string, content = `Content for ${id}`) {
  return {
    id,
    title,
    content,
    citations: [],
    platform_level_estimate: ["Q", "S", "T"].includes(id),
    manual_review: false,
    unverified_citations: false,
  };
}

const planDocument: PlanDocument = {
  source: "teradata",
  target: "snowflake",
  overall_confidence: 0.9,
  sections: [
    makeSection("Q", "Effort Estimation"),
    makeSection("S", "Confidence Scoring"),
    makeSection("T", "Expert Review Items"),
  ],
};

function planStatus(overrides: Partial<PlanStatus> = {}): PlanStatus {
  return {
    state: "done",
    progress: { phase: "done", elapsed_s: 14, devin_url: null },
    plan: planDocument,
    origin: "devin",
    reason: null,
    cached: false,
    kb_version: 4,
    stale: false,
    inventory_stale: false,
    created_at: "2026-10-15T12:00:00Z",
    ...overrides,
  };
}

function sessionWithPlan(plan: PlanStatus): SessionResponse {
  return { session_id: "session-1", source: "teradata", target: "snowflake", plan };
}

function kbStatus(overrides: Partial<KBStatusResponse> = {}): KBStatusResponse {
  return {
    current_version: 4,
    created_at: "2026-10-15T12:00:00Z",
    documents: 102,
    chunks: 333,
    last_refresh_summary: null,
    running: null,
    rollback_versions: [3, 2],
    seed_error: null,
    ...overrides,
  };
}

describe("Phase 2 W4 frontend", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    apiMocks.apiRequest.mockResolvedValue({});
    apiMocks.exchangeLaunchToken.mockResolvedValue(undefined);
    apiMocks.getPlan.mockResolvedValue(planStatus());
    apiMocks.startPlan.mockResolvedValue(planStatus());
    apiMocks.cancelPlanJob.mockResolvedValue(planStatus({ state: "cancelled" }));
    apiMocks.getResources.mockResolvedValue([]);
    apiMocks.updateResourceAvailability.mockResolvedValue({ id: "tables", available: true });
    apiMocks.getKBStatus.mockResolvedValue(kbStatus());
    apiMocks.startKBRefresh.mockResolvedValue({ status: "started" });
    apiMocks.cancelKBRefresh.mockResolvedValue({ status: "cancelling" });
    apiMocks.rollbackKB.mockResolvedValue({ current_version: 3 });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows the local fallback banner and starts a fresh attempt on Retry", async () => {
    const user = userEvent.setup();
    apiMocks.startPlan.mockResolvedValue(
      planStatus({
        state: "running",
        progress: { phase: "running", elapsed_s: 3, devin_url: null },
        plan: null,
      }),
    );
    const initial = planStatus({
      state: "failed_fallback",
      progress: { phase: "failed_fallback", elapsed_s: 0, devin_url: null },
      origin: "local_draft",
      reason: "Devin token not configured",
      plan: null,
    });
    render(
      <PlanningPanel
        confidenceThreshold={0.85}
        session={sessionWithPlan(initial)}
        sourceName="Teradata"
        targetName="Snowflake"
      />,
    );

    expect(screen.getByText("Local draft (Devin unavailable)")).toBeInTheDocument();
    expect(screen.getByText("Devin token not configured")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(apiMocks.startPlan).toHaveBeenCalledWith(true);
  });

  it("shows the saved cached-plan note and confirms ACU use before regenerating", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    apiMocks.startPlan.mockResolvedValue(
      planStatus({
        state: "running",
        progress: { phase: "creating_session", elapsed_s: 1, devin_url: null },
        plan: null,
      }),
    );
    render(
      <PlanningPanel
        confidenceThreshold={0.85}
        session={sessionWithPlan(planStatus({ cached: true }))}
        sourceName="Teradata"
        targetName="Snowflake"
      />,
    );

    expect(screen.getByText(/Saved plan · KB v4 ·/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Regenerate" }));
    expect(confirm).toHaveBeenCalledWith(
      "Regenerate this plan? This starts a new Devin session and uses ACUs.",
    );
    expect(apiMocks.startPlan).toHaveBeenCalledWith(true);
  });

  it("stops polling when the plan reaches a terminal state", async () => {
    vi.useFakeTimers();
    apiMocks.getPlan.mockResolvedValue(planStatus());
    const initial = planStatus({
      state: "running",
      progress: { phase: "running", elapsed_s: 2, devin_url: null },
      plan: null,
    });
    render(
      <PlanningPanel
        confidenceThreshold={0.85}
        session={sessionWithPlan(initial)}
        sourceName="Teradata"
        targetName="Snowflake"
      />,
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(apiMocks.getPlan).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000);
    });
    expect(apiMocks.getPlan).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });

  it("shows platform-level estimate badges on Q, S, and T", async () => {
    const user = userEvent.setup();
    render(
      <PlanningPanel
        confidenceThreshold={0.85}
        session={sessionWithPlan(planStatus())}
        sourceName="Teradata"
        targetName="Snowflake"
      />,
    );

    expect(screen.getAllByText("Platform-level estimate")).toHaveLength(1);
    await user.click(screen.getByRole("tab", { name: /S Confidence Scoring/ }));
    expect(screen.getAllByText("Platform-level estimate")).toHaveLength(1);
    await user.click(screen.getByRole("tab", { name: /T Expert Review Items/ }));
    expect(screen.getAllByText("Platform-level estimate")).toHaveLength(1);
  });

  it("does not render raw script HTML from plan Markdown", () => {
    const unsafePlan = planStatus({
      plan: {
        ...planDocument,
        sections: [makeSection("Q", "Effort Estimation", "<script>alert(1)</script>\n\nSafe text")],
      },
    });
    const { container } = render(
      <PlanningPanel
        confidenceThreshold={0.85}
        session={sessionWithPlan(unsafePlan)}
        sourceName="Teradata"
        targetName="Snowflake"
      />,
    );

    expect(container.querySelector("script")).toBeNull();
    expect(screen.getByText("Safe text")).toBeInTheDocument();
  });

  it("offers inventory regeneration only for a stale plan and posts regenerate", async () => {
    const user = userEvent.setup();
    const stalePlan = planStatus({ inventory_stale: true });
    apiMocks.startPlan.mockResolvedValue(planStatus({ inventory_stale: false }));
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const { rerender } = render(
      <PlanningPanel
        confidenceThreshold={0.85}
        session={sessionWithPlan(planStatus())}
        sourceName="Teradata"
        targetName="Snowflake"
      />,
    );

    expect(screen.queryByRole("button", { name: "Regenerate with inventory" })).toBeNull();
    rerender(
      <PlanningPanel
        confidenceThreshold={0.85}
        session={sessionWithPlan(stalePlan)}
        sourceName="Teradata"
        targetName="Snowflake"
      />,
    );
    const regenerate = screen.getByRole("button", { name: "Regenerate with inventory" });
    await user.click(regenerate);
    expect(confirm).toHaveBeenCalledWith(
      "Regenerate this plan? This starts a new Devin session and uses ACUs.",
    );
    expect(apiMocks.startPlan).toHaveBeenCalledWith(true);

    rerender(
      <PlanningPanel
        confidenceThreshold={0.85}
        session={sessionWithPlan(planStatus())}
        sourceName="Teradata"
        targetName="Snowflake"
      />,
    );
    expect(screen.queryByRole("button", { name: "Regenerate with inventory" })).toBeNull();
    confirm.mockRestore();
  });

  it("disables KB refresh while a refresh job is running", async () => {
    apiMocks.getKBStatus.mockResolvedValue(
      kbStatus({
        running: {
          phase: "crawling",
          pages_fetched: 42,
          pages_skipped: 0,
          pages_failed: 0,
          chunks: 12,
          elapsed: 3,
          running: true,
        },
      }),
    );
    render(<KnowledgeBaseSettings active />);

    expect(await screen.findByRole("button", { name: "Refresh" })).toBeDisabled();
    expect(screen.getByText("42 / 3,000 pages")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeEnabled();
  });

  it("keeps the Devin organization ID optional and shows the legacy-key hint", () => {
    render(
      <SettingsDialog
        onClose={() => undefined}
        onSave={async () => undefined}
        open
        settings={settings}
      />,
    );

    expect(screen.getByLabelText(/Organization ID/)).not.toBeRequired();
    expect(screen.getByText(/Not needed for legacy/)).toHaveTextContent("apk_");
  });
});
