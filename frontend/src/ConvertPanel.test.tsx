import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConvertPanel } from "./ConvertPanel";
import { ConversionDetail } from "./ConversionDetail";
import type {
  ConversionDetailResponse,
  ConversionJobStatus,
  ConversionRow,
  ConvertEstimate,
} from "./api";

const apiMocks = vi.hoisted(() => ({
  cancelConversionJob: vi.fn(),
  downloadConvertedPackage: vi.fn(),
  downloadReport: vi.fn(),
  getConversionDetail: vi.fn(),
  getConversionJob: vi.fn(),
  getConversions: vi.fn(),
  postConvertEstimate: vi.fn(),
  startConversion: vi.fn(),
}));

vi.mock("./api", () => ({ ...apiMocks }));

const estimate: ConvertEstimate = {
  objects: 2,
  batches: 2,
  oversized_batches: 0,
  voter_sessions: 6,
  max_escalation_sessions: 2,
  max_sessions: 8,
  batch_size: 20,
  session_timeout_minutes: 45,
  max_concurrent_sessions: 4,
  devin_configured: true,
  devin_host: "api.devin.ai",
  acu_cap: null,
};

const row: ConversionRow = {
  object_id: "object-1",
  qualified_name: "sales.orders",
  type: "table",
  status: "converted",
  confidence: 0.95,
  agreement: 0.9,
  pass_rate: 1,
  escalated: false,
  checks: { syntax: "passed", lint: "not_applicable" },
};

const detail: ConversionDetailResponse = {
  final: {
    files: [{ rel_path: "orders.sql", language: "sql", content: "SELECT 1;\n" }],
    explanations: ["Converted to Snowflake SQL."],
    assumptions: ["The source uses UTC."],
    unsupported: ["No unsupported constructs."],
  },
  picked_voter: "A",
  candidates: {
    A: {
      files: [{ rel_path: "orders.sql", language: "sql", content: "SELECT 2;\n" }],
      self_confidence: 0.9,
      state: "done",
      error: null,
    },
    B: null,
    C: null,
  },
  pairwise: { "A:B": 0.9 },
  checks: [{
    check: "syntax",
    status: "passed",
    findings: [{ check: "syntax", rule_id: "snowflake.syntax.valid", severity: "warning", message: "No issues.", line: 2 }],
  }],
  escalation: null,
  devin_sessions: [{ role: "A", url: "https://devin.ai/session/1", state: "done", acus: null }],
};

function runningJob(overrides: Partial<ConversionJobStatus> = {}): ConversionJobStatus {
  return {
    run_id: "job-1",
    state: "running",
    progress: {
      elapsed_s: 2,
      batches: {
        "0": { status: "running", objects: ["object-1"], voters: { A: "done", B: "running", C: "queued", escalation: "not_needed" }, escalated: false },
      },
    },
    batches: [{
      index: 0,
      status: "running",
      objects: ["object-1"],
      voters: { A: "done", B: "running", C: "queued", escalation: "not_needed" },
      escalated: false,
      object_count: 1,
      counts: { converted: 0, escalated: 0, expert_review: 0, failed: 0 },
    }],
    counts: { converted: 0, escalated: 0, expert_review: 0, failed: 0 },
    object_count: 1,
    batch_count: 1,
    reason: null,
    ...overrides,
  };
}

describe("Convert Objects", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    window.localStorage.clear();
    apiMocks.postConvertEstimate.mockResolvedValue(estimate);
    apiMocks.startConversion.mockResolvedValue({ job_id: "job-1" });
    apiMocks.getConversionJob.mockResolvedValue(runningJob());
    apiMocks.cancelConversionJob.mockResolvedValue(runningJob({ state: "cancelled" }));
    apiMocks.downloadConvertedPackage.mockResolvedValue(undefined);
    apiMocks.downloadReport.mockResolvedValue(undefined);
    apiMocks.getConversions.mockResolvedValue({ items: [row], page: 1, page_size: 200, total: 1 });
    apiMocks.getConversionDetail.mockResolvedValue(detail);
  });

  it("shows the consent copy and requires the checkbox before sending", async () => {
    const user = userEvent.setup();
    render(<ConvertPanel confirmed refreshKey={1} source="teradata" target="snowflake" />);
    await user.click(await screen.findByRole("button", { name: "Convert Items" }));

    const dialog = screen.getByRole("dialog");
    expect(screen.getByRole("heading", { name: "Send code to Devin?" })).toBeInTheDocument();
    expect(dialog).toHaveTextContent(
      "Converting sends the source code of 2 selected objects to Devin at api.devin.ai, in 6 sessions (up to 8 if low-confidence objects need escalation). These sessions have no ACU limit. Each one stops after 45 minutes, or when you cancel. Nothing else from your files, your settings or your Devin key is sent. Your consent is recorded in the audit log.",
    );
    const send = screen.getByRole("button", { name: "Send to Devin" });
    expect(screen.getByLabelText("I understand that this code leaves this computer")).not.toBeChecked();
    expect(send).toBeDisabled();
    await user.click(screen.getByLabelText("I understand that this code leaves this computer"));
    expect(send).toBeEnabled();
    await user.click(send);
    await waitFor(() => expect(apiMocks.startConversion).toHaveBeenCalledOnce());
  });

  it("explains that Convert Items is waiting for its estimate", async () => {
    const user = userEvent.setup();
    let resolveEstimate: (value: ConvertEstimate) => void = () => undefined;
    apiMocks.postConvertEstimate.mockReturnValue(
      new Promise((resolve) => {
        resolveEstimate = resolve;
      }),
    );
    render(<ConvertPanel confirmed refreshKey={1} source="teradata" target="snowflake" />);

    const button = await screen.findByRole("button", { name: "Estimating…" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("title", "Waiting for the conversion estimate.");
    await user.click(button);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await act(async () => resolveEstimate(estimate));
    await user.click(await screen.findByRole("button", { name: "Convert Items" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("shows the exact 412 message without sending a conversion request", async () => {
    const user = userEvent.setup();
    apiMocks.postConvertEstimate.mockResolvedValue({ ...estimate, devin_configured: false });
    render(<ConvertPanel confirmed refreshKey={1} source="teradata" target="snowflake" />);

    await user.click(await screen.findByRole("button", { name: "Convert Items" }));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Devin isn't configured. Add a Devin API key in Settings to convert; nothing was sent.",
    );
    expect(apiMocks.startConversion).not.toHaveBeenCalled();
  });

  it("renders voter progress and confirms before cancelling", async () => {
    const user = userEvent.setup();
    render(<ConvertPanel confirmed refreshKey={1} source="teradata" target="snowflake" />);
    await user.click(await screen.findByRole("button", { name: "Convert Items" }));
    await user.click(screen.getByRole("checkbox", { name: "I understand that this code leaves this computer" }));
    await user.click(screen.getByRole("button", { name: "Send to Devin" }));

    expect(await screen.findByText("A: done")).toBeInTheDocument();
    expect(screen.getByText("B: running")).toBeInTheDocument();
    expect(screen.getByText("C: queued")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("Cancel this conversion?");
    await user.click(screen.getByRole("button", { name: "Cancel conversion" }));
    await waitFor(() => expect(apiMocks.cancelConversionJob).toHaveBeenCalledWith("job-1"));
  });

  it("closes the cancellation confirmation when a job becomes terminal", async () => {
    const user = userEvent.setup();
    apiMocks.getConversionJob
      .mockResolvedValueOnce(runningJob())
      .mockResolvedValue(runningJob({ state: "done" }));
    render(<ConvertPanel confirmed refreshKey={1} source="teradata" target="snowflake" />);

    await user.click(await screen.findByRole("button", { name: "Convert Items" }));
    await user.click(
      screen.getByRole("checkbox", { name: "I understand that this code leaves this computer" }),
    );
    await user.click(screen.getByRole("button", { name: "Send to Devin" }));
    await screen.findByText("A: done");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("Cancel this conversion?");

    await waitFor(
      () => {
        expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Keep converting" })).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Cancel conversion" })).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
      },
      { timeout: 3000 },
    );
  });

  it("passes the status, type, failed-check and search filters to the results API", async () => {
    const user = userEvent.setup();
    render(<ConvertPanel confirmed refreshKey={1} source="teradata" target="snowflake" />);
    await screen.findByText("sales.orders");
    await user.selectOptions(screen.getByRole("combobox", { name: "Filter by status" }), "converted");
    await user.selectOptions(screen.getByRole("combobox", { name: "Filter by type" }), "table");
    await user.selectOptions(screen.getByRole("combobox", { name: "Filter by failed check" }), "syntax");
    await user.type(screen.getByRole("textbox", { name: "Search converted objects" }), "sales");
    await waitFor(() => {
      expect(apiMocks.getConversions).toHaveBeenLastCalledWith(
        expect.objectContaining({
          status: "converted",
          type: "table",
          failed_check: "syntax",
          search: "sales",
        }),
      );
    });
  });

  it("scrolls results and the selected detail within the dashboard", async () => {
    const user = userEvent.setup();
    const dashboardScroll = vi.fn();
    apiMocks.getConversionJob.mockResolvedValue(runningJob({ state: "done" }));
    render(
      <main className="dashboard" ref={(element) => {
        if (element) element.scrollTo = dashboardScroll;
      }}>
        <ConvertPanel confirmed refreshKey={1} source="teradata" target="snowflake" />
      </main>,
    );

    await user.click(await screen.findByRole("button", { name: "Convert Items" }));
    await user.click(screen.getByRole("checkbox", { name: "I understand that this code leaves this computer" }));
    await user.click(screen.getByRole("button", { name: "Send to Devin" }));
    await waitFor(() => expect(dashboardScroll).toHaveBeenCalled());
    expect(dashboardScroll).toHaveBeenCalledWith(expect.objectContaining({ behavior: "smooth" }));

    dashboardScroll.mockClear();
    await user.click(await screen.findByRole("button", { name: "sales.orders" }));
    await waitFor(() => expect(dashboardScroll).toHaveBeenCalledOnce());
    expect(dashboardScroll).toHaveBeenCalledWith(expect.objectContaining({ behavior: "smooth" }));
  });

  it("shows candidate diffs and copies a file", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup();
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    render(<ConversionDetail detail={detail} />);
    await user.click(screen.getByRole("tab", { name: "A" }));

    expect(screen.getByText("SELECT 1;")).toBeInTheDocument();
    expect(screen.getByText("SELECT 2;")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Copy" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("SELECT 2;\n"));
  });

  it("pairs candidate and final files by index when their paths differ", async () => {
    const detailWithDifferentPaths: ConversionDetailResponse = {
      ...detail,
      final: {
        ...detail.final,
        files: [
          {
            rel_path: "security.a.sql",
            language: "sql",
            etl_format_id: "sql",
            content: "-- Converted: grant\n-- Reason: preserve\nGRANT SELECT ON x TO operator;\n",
          },
          {
            rel_path: "audit.a.sql",
            language: "sql",
            etl_format_id: "sql",
            content: "-- Converted: audit\n-- Reason: preserve\nGRANT INSERT ON x TO operator;\n",
          },
          {
            rel_path: "report.a.sql",
            language: "sql",
            content: "-- Converted: report\n-- Reason: preserve\nGRANT DELETE ON x TO operator;\n",
          },
        ],
      },
      candidates: {
        ...detail.candidates,
        B: {
          ...detail.candidates.B!,
          files: [
            {
              rel_path: "security.b.sql",
              language: "sql",
              etl_format_id: "sql",
              content: "-- Converted: grant\n-- Reason: preserve\nGRANT EXECUTE ON x TO operator;\n",
            },
            {
              rel_path: "audit.b.sql",
              language: "sql",
              etl_format_id: "sql",
              content: "-- Converted: audit\n-- Reason: preserve\nGRANT UPDATE ON x TO operator;\n",
            },
            {
              rel_path: "report.b.sql",
              language: "sql",
              content: "-- Converted: report\n-- Reason: preserve\nGRANT CREATE ON x TO operator;\n",
            },
          ],
        },
      },
    };
    const user = userEvent.setup();
    render(<ConversionDetail detail={detailWithDifferentPaths} />);
    await user.click(screen.getByRole("tab", { name: "B" }));

    const firstDiff = screen.getByLabelText("security.b.sql B diff");
    expect(firstDiff.querySelectorAll(".diff-removed")).toHaveLength(1);
    expect(firstDiff.querySelectorAll(".diff-added")).toHaveLength(1);
    expect(firstDiff).toHaveTextContent("GRANT SELECT ON x TO operator;");
    expect(firstDiff).toHaveTextContent("GRANT EXECUTE ON x TO operator;");

    const secondDiff = screen.getByLabelText("audit.b.sql B diff");
    expect(secondDiff.querySelectorAll(".diff-removed")).toHaveLength(1);
    expect(secondDiff.querySelectorAll(".diff-added")).toHaveLength(1);
    expect(secondDiff).toHaveTextContent("GRANT INSERT ON x TO operator;");
    expect(secondDiff).toHaveTextContent("GRANT UPDATE ON x TO operator;");

    const languageFallbackDiff = screen.getByLabelText("report.b.sql B diff");
    expect(languageFallbackDiff.querySelectorAll(".diff-removed")).toHaveLength(1);
    expect(languageFallbackDiff.querySelectorAll(".diff-added")).toHaveLength(1);
    expect(languageFallbackDiff).toHaveTextContent("GRANT DELETE ON x TO operator;");
    expect(languageFallbackDiff).toHaveTextContent("GRANT CREATE ON x TO operator;");
  });

  it("labels candidate files that have no matching final file", async () => {
    const detailWithNewFile: ConversionDetailResponse = {
      ...detail,
      final: {
        ...detail.final,
        files: [{ rel_path: "final.sql", language: "sql", content: "SELECT 1;\n" }],
      },
      candidates: {
        ...detail.candidates,
        B: {
          ...detail.candidates.B!,
          files: [{ rel_path: "new.py", language: "python", content: "print(1)\n" }],
        },
      },
    };
    const user = userEvent.setup();
    render(<ConversionDetail detail={detailWithNewFile} />);
    await user.click(screen.getByRole("tab", { name: "B" }));

    expect(screen.getByText("New file (no matching final file)")).toBeInTheDocument();
    expect(screen.getByLabelText("new.py B diff").querySelectorAll(".diff-added")).toHaveLength(0);
  });

  it("keeps Download disabled and explains why before a conversion exists", async () => {
    render(<ConvertPanel confirmed refreshKey={1} source="teradata" target="snowflake" />);
    const download = await screen.findByRole("button", { name: /Download Converted Files/ });
    expect(download).toBeDisabled();
    expect(download).toHaveAttribute("title", "Run a conversion before downloading.");
  });

  it("keeps Download disabled after a run with no included objects", async () => {
    window.localStorage.setItem("migraai.activeConversionJob", "job-1");
    apiMocks.getConversionJob.mockResolvedValue(
      runningJob({
        state: "done",
        counts: { converted: 0, escalated: 0, expert_review: 0, failed: 1 },
      }),
    );
    render(<ConvertPanel confirmed refreshKey={1} source="teradata" target="snowflake" />);

    const download = await screen.findByRole("button", { name: /Download Converted Files/ });
    await waitFor(() => expect(download).toBeDisabled());
    expect(download).toHaveAttribute(
      "title",
      "No converted or expert-review objects are available to download.",
    );
  });

  it("shows the optional package name only for multiple uploads", async () => {
    const { rerender } = render(
      <ConvertPanel
        confirmed
        refreshKey={1}
        source="teradata"
        target="snowflake"
        uploadCount={1}
      />,
    );
    expect(screen.queryByRole("textbox", { name: "Package name" })).not.toBeInTheDocument();

    rerender(
      <ConvertPanel
        confirmed
        refreshKey={1}
        source="teradata"
        target="snowflake"
        uploadCount={2}
      />,
    );
    expect(await screen.findByRole("textbox", { name: "Package name" })).toHaveValue(
      "migraai-teradata-to-snowflake",
    );
    expect(screen.getByRole("link", { name: "Report (MD)" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Report (PDF)" })).toBeInTheDocument();
  });

  it("enables Download for completed included objects and shows a 409 inline", async () => {
    const user = userEvent.setup();
    const complete = runningJob({
      state: "done",
      counts: { converted: 1, escalated: 0, expert_review: 0, failed: 0 },
    });
    window.localStorage.setItem("migraai.activeConversionJob", "job-1");
    apiMocks.getConversionJob.mockResolvedValue(complete);
    apiMocks.downloadConvertedPackage.mockRejectedValue(
      new Error("The conversion has no converted or expert-review objects to download."),
    );
    render(
      <ConvertPanel
        confirmed
        refreshKey={1}
        source="teradata"
        target="snowflake"
        uploadCount={2}
      />,
    );

    const download = await screen.findByRole("button", { name: /Download Converted Files/ });
    await waitFor(() => expect(download).toBeEnabled());
    await user.click(download);
    expect(apiMocks.downloadConvertedPackage).toHaveBeenCalledWith(
      "migraai-teradata-to-snowflake",
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The conversion has no converted or expert-review objects to download.",
    );
  });

  it("shows a report 503 inline", async () => {
    const user = userEvent.setup();
    apiMocks.downloadReport.mockRejectedValue(new Error("Report generator not available yet."));
    render(
      <ConvertPanel
        confirmed
        refreshKey={1}
        source="teradata"
        target="snowflake"
        uploadCount={2}
      />,
    );

    await user.click(screen.getByRole("link", { name: "Report (MD)" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Report generator not available yet.",
    );
  });
});
