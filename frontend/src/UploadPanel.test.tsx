import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
    mocks.uploadFiles.mockResolvedValue([upload]);
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
    expect(screen.getByText("Unsupported file format.")).toBeInTheDocument();

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

    const checkbox = await screen.findByRole("checkbox", { name: "Select tables.sql" });
    await user.click(checkbox);
    await waitFor(() =>
      expect(mocks.setUploadSelected).toHaveBeenCalledWith("upload-1", false),
    );
    await user.click(screen.getByRole("button", { name: "Delete tables.sql" }));
    expect(window.confirm).toHaveBeenCalledWith("Delete tables.sql?");
    expect(mocks.deleteUpload).toHaveBeenCalledWith("upload-1");
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

    expect(screen.getByText("Discovery complete.")).toBeInTheDocument();
  });
});
