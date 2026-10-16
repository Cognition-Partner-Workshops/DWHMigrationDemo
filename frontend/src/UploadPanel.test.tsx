import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  deleteUpload: vi.fn(),
  getUploads: vi.fn(),
  setUploadSelected: vi.fn(),
  uploadFiles: vi.fn(),
  watchDiscoveryStatus: vi.fn(),
}));

vi.mock("./api", () => mocks);

import { UploadPanel } from "./UploadPanel";

const upload = {
  id: "upload-1",
  rel_path: "tables.sql",
  size: 12,
  detected_type: "sql:ansi",
  status: "done",
  message: null,
  selected: true,
  object_count: 3,
  issue_count: 1,
};

describe("UploadPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getUploads.mockResolvedValue([]);
    mocks.uploadFiles.mockResolvedValue({ uploads: [upload], replaced: [] });
    mocks.setUploadSelected.mockResolvedValue({ ...upload, selected: false });
    mocks.deleteUpload.mockResolvedValue({ deleted: true });
    mocks.watchDiscoveryStatus.mockReturnValue(() => undefined);
    vi.spyOn(window, "confirm").mockReturnValue(true);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows local rejected files and uploads only supported files", async () => {
    const user = userEvent.setup();
    const onUploadsChange = vi.fn();
    render(
      <UploadPanel
        confirmed
        onInventoryChanged={vi.fn()}
        onUploadsChange={onUploadsChange}
        uploads={[]}
      />,
    );

    const input = screen.getByLabelText("Choose files to upload");
    fireEvent.change(input, {
      target: {
        files: [
          new File(["bad"], "readme.exe"),
          new File(["select 1"], "tables.sql"),
        ],
      },
    });
    expect(screen.getByText("Rejected")).toBeInTheDocument();
    expect(screen.getByText("Rejected").closest("td")).toHaveClass("upload-status-cell");
    expect(screen.getByText("Unsupported file format.")).toBeInTheDocument();
    expect(screen.getByText("Unsupported file format.")).toHaveAttribute(
      "title",
      "Unsupported file format.",
    );

    await user.click(screen.getByRole("button", { name: "Upload Files" }));
    await waitFor(() => expect(mocks.uploadFiles).toHaveBeenCalledOnce());
    expect(mocks.uploadFiles.mock.calls[0][0].map((file: File) => file.name)).toEqual([
      "tables.sql",
    ]);
    expect(screen.getByText("Issues")).toBeInTheDocument();
    expect(onUploadsChange).toHaveBeenCalled();
  });

  it("selects and deletes uploaded rows after confirmation", async () => {
    const user = userEvent.setup();
    mocks.getUploads.mockResolvedValue([upload]);
    const onUploadsChange = vi.fn();
    render(
      <UploadPanel
        confirmed
        onInventoryChanged={vi.fn()}
        onUploadsChange={onUploadsChange}
        uploads={[upload]}
      />,
    );

    await user.click(
      await screen.findByRole("button", {
        name: "1 file · Discovery complete · Show files ▾",
      }),
    );
    const checkbox = await screen.findByRole("checkbox", { name: "Select tables.sql" });
    await user.click(checkbox);
    await waitFor(() =>
      expect(mocks.setUploadSelected).toHaveBeenCalledWith("upload-1", false),
    );
    await user.click(screen.getByRole("button", { name: "Delete tables.sql" }));
    expect(window.confirm).toHaveBeenCalledWith("Delete tables.sql?");
    expect(mocks.deleteUpload).toHaveBeenCalledWith("upload-1");
  });

  it("removes replaced upload rows and shows a replacement notice", async () => {
    const user = userEvent.setup();
    const onUploadsChange = vi.fn();
    const replacement = { ...upload, id: "upload-new", rel_path: "tables.sql" };
    let reportStatus: ((snapshot: {
      status: { state: string; files_total: number; files_completed: number; current_file: null };
      uploads: typeof upload[];
    }) => void) | undefined;
    mocks.getUploads.mockResolvedValueOnce([]).mockResolvedValueOnce([upload, replacement]);
    mocks.uploadFiles.mockResolvedValue({
      uploads: [replacement],
      replaced: ["upload-1"],
    });
    mocks.watchDiscoveryStatus.mockImplementation((onStatus: typeof reportStatus) => {
      reportStatus = onStatus;
      return () => undefined;
    });
    render(
      <UploadPanel
        confirmed
        onInventoryChanged={vi.fn()}
        onUploadsChange={onUploadsChange}
        uploads={[{ ...upload, status: "parsing" }]}
      />,
    );

    fireEvent.change(screen.getByLabelText("Choose files to upload"), {
      target: { files: [new File(["select 1"], "tables.sql")] },
    });
    await user.click(screen.getByRole("button", { name: "Upload Files" }));

    expect(await screen.findByText("Replaced 1 earlier file with the same path."))
      .toBeInTheDocument();
    expect(onUploadsChange).toHaveBeenLastCalledWith([replacement]);

    act(() => {
      reportStatus?.({
        status: { state: "done", files_total: 1, files_completed: 1, current_file: null },
        uploads: [replacement],
      });
    });
    expect(screen.getByText("Discovery done")).toBeInTheDocument();
    expect(screen.getByText("Replaced 1 earlier file with the same path.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Dismiss replacement notice" }));
    expect(screen.queryByText("Replaced 1 earlier file with the same path.")).toBeNull();
  });

  it("replaces the processing notice when discovery completes", async () => {
    const user = userEvent.setup();
    let reportStatus: ((snapshot: {
      status: { state: string; files_total: number; files_completed: number; current_file: null };
      uploads: typeof upload[];
    }) => void) | undefined;
    mocks.getUploads.mockResolvedValue([upload]);
    mocks.watchDiscoveryStatus.mockImplementation((onStatus: typeof reportStatus) => {
      reportStatus = onStatus;
      return () => undefined;
    });
    render(
      <UploadPanel
        confirmed
        onInventoryChanged={vi.fn()}
        onUploadsChange={vi.fn()}
        uploads={[]}
      />,
    );

    fireEvent.change(screen.getByLabelText("Choose files to upload"), {
      target: { files: [new File(["select 1"], "tables.sql")] },
    });
    await user.click(screen.getByRole("button", { name: "Upload Files" }));
    await waitFor(() => expect(mocks.watchDiscoveryStatus).toHaveBeenCalledOnce());
    expect(screen.getByText("Files uploaded. Discovery is processing the selected files."))
      .toBeInTheDocument();

    reportStatus?.({
      status: { state: "done", files_total: 1, files_completed: 1, current_file: null },
      uploads: [upload],
    });
    expect(await screen.findByText("Discovery complete.")).toBeInTheDocument();
  });

  it("shows completed discovery when all restored uploads are terminal", () => {
    render(
      <UploadPanel
        confirmed
        onInventoryChanged={vi.fn()}
        onUploadsChange={vi.fn()}
        uploads={[upload]}
      />,
    );

    expect(
      screen.getByRole("button", {
        name: "1 file · Discovery complete · Show files ▾",
      }),
    ).toHaveAttribute("aria-expanded", "false");
  });

  it("collapses completed uploads into a summary and expands the file list", async () => {
    const user = userEvent.setup();
    const uploads = Array.from({ length: 7 }, (_, index) => ({
      ...upload,
      id: `upload-${index}`,
      rel_path: `tables-${index}.sql`,
    }));
    mocks.getUploads.mockResolvedValue(uploads);

    render(
      <UploadPanel
        confirmed
        onInventoryChanged={vi.fn()}
        onUploadsChange={vi.fn()}
        uploads={uploads}
      />,
    );

    const summary = screen.getByRole("button", {
      name: "7 files · Discovery complete · Show files ▾",
    });
    expect(summary).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("region", { name: "Uploaded files" })).not.toBeInTheDocument();

    await user.click(summary);
    expect(
      screen.getByRole("button", {
        name: "7 files · Discovery complete · Hide files ▴",
      }),
    ).toHaveAttribute("aria-expanded", "true");
    const uploadRegion = screen.getByRole("region", { name: "Uploaded files" });
    expect(uploadRegion.querySelectorAll("tbody tr")).toHaveLength(7);
  });

  it.each(["rejected", "failed"] as const)("keeps %s uploads expanded", (status) => {
    render(
      <UploadPanel
        confirmed
        onInventoryChanged={vi.fn()}
        onUploadsChange={vi.fn()}
        uploads={[{ ...upload, status }]}
      />,
    );

    expect(screen.getByRole("region", { name: "Uploaded files" })).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Discovery complete.*Show files/ }),
    ).not.toBeInTheDocument();
  });
});
