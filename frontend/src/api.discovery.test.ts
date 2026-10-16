import { afterEach, describe, expect, it, vi } from "vitest";

import {
  distinctUploadRoots,
  getInventory,
  uploadFiles,
  uploadRoot,
  watchDiscoveryStatus,
} from "./api";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("discovery API client", () => {
  it("groups upload paths by their first ZIP root", () => {
    expect(uploadRoot("teradata-testpack.zip/tables.sql")).toBe("teradata-testpack.zip");
    expect(uploadRoot("A.ZIP/x/y.sql")).toBe("A.ZIP");
    expect(uploadRoot("a.zip/inner.zip/x.sql")).toBe("a.zip");
    expect(uploadRoot("dir\\b.sql")).toBe("dir/b.sql");
    expect(
      distinctUploadRoots([
        { rel_path: "teradata-testpack.zip/tables.sql" },
        { rel_path: "teradata-testpack.zip/views.sql" },
        { rel_path: "extra.sql" },
      ]),
    ).toEqual(["teradata-testpack.zip", "extra.sql"]);
  });

  it("uploads multiple files with multipart form data and reports progress", async () => {
    const listeners = new Map<string, (event: ProgressEvent) => void>();
    const progressListeners = new Map<string, (event: ProgressEvent) => void>();
    const formDataBodies: FormData[] = [];
    const xhr = {
      status: 200,
      responseText: JSON.stringify({
        uploads: [
          {
            id: "upload-1",
            rel_path: "one.sql",
            size: 10,
            detected_type: "sql:ansi",
            status: "pending",
            message: null,
            selected: true,
            object_count: 0,
          },
        ],
        replaced: ["upload-old"],
      }),
      withCredentials: false,
      open: vi.fn(),
      setRequestHeader: vi.fn(),
      addEventListener: vi.fn((type: string, listener: (event: ProgressEvent) => void) => {
        listeners.set(type, listener);
      }),
      upload: {
        addEventListener: vi.fn((type: string, listener: (event: ProgressEvent) => void) => {
          progressListeners.set(type, listener);
        }),
      },
      send: vi.fn((body: FormData) => {
        formDataBodies.push(body);
      }),
    } as unknown as XMLHttpRequest;
    vi.stubGlobal(
      "XMLHttpRequest",
      vi.fn(function XMLHttpRequestMock() {
        return xhr;
      }),
    );
    const progress = vi.fn();

    const result = uploadFiles(
      [new File(["select 1"], "one.sql"), new File(["select 2"], "two.sql")],
      progress,
    );

    expect(xhr.open).toHaveBeenCalledWith("POST", "/api/uploads");
    expect(xhr.withCredentials).toBe(true);
    expect(xhr.setRequestHeader).toHaveBeenCalledWith("Accept", "application/json");
    const formData = formDataBodies[0];
    expect(formData.getAll("files")).toHaveLength(2);
    progressListeners.get("progress")?.({
      lengthComputable: true,
      loaded: 50,
      total: 100,
    } as ProgressEvent);
    expect(progress).toHaveBeenCalledWith(50, 100);

    listeners.get("load")?.(new ProgressEvent("load"));
    await expect(result).resolves.toMatchObject({
      uploads: [{ id: "upload-1" }],
      replaced: ["upload-old"],
    });
  });

  it("stops polling when discovery is idle", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith("/api/discovery/status")) {
        return Response.json({
          state: "idle",
          files_total: 0,
          files_completed: 0,
          current_file: null,
        });
      }
      return Response.json([]);
    });
    vi.stubGlobal("fetch", fetchMock);
    const onUpdate = vi.fn();
    const stop = watchDiscoveryStatus(onUpdate, vi.fn());

    await vi.advanceTimersByTimeAsync(0);
    expect(onUpdate).toHaveBeenCalledOnce();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(2000);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    stop();
  });

  it("serializes inventory filters and page parameters", async () => {
    const fetchMock = vi.fn(async () =>
      Response.json({ items: [], page: 2, page_size: 50, total: 0, external_total: 0 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await getInventory({
      q: "daily orders",
      type: "table",
      status: "parsed",
      file: "warehouse.sql",
      page: 2,
      page_size: 50,
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/inventory?q=daily+orders&type=table&status=parsed&file=warehouse.sql&page=2&page_size=50",
      expect.objectContaining({ method: "GET" }),
    );
  });
});
