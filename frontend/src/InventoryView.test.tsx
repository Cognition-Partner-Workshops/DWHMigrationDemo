import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getGraph: vi.fn(),
  getInventory: vi.fn(),
  getInventoryDetail: vi.fn(),
}));

vi.mock("./api", () => mocks);
vi.mock("./GraphView", () => ({
  GraphView: () => <div>Mock dependency graph</div>,
}));

import { InventoryView } from "./InventoryView";

describe("InventoryView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getInventory.mockResolvedValue({
      items: [],
      page: 1,
      page_size: 50,
      total: 120,
      external_total: 0,
    });
    mocks.getInventoryDetail.mockResolvedValue({
      id: "object-1",
      upload_id: "upload-1",
      file: "tables.sql",
      type: "table",
      schema_name: "dw",
      name: "orders",
      qualified_name: "dw.orders",
      status: "parsed",
      start_line: 1,
      end_line: 3,
      source_preview: "CREATE TABLE dw.orders;",
      external: false,
      issues: [],
      upstream: [],
      downstream: [],
    });
  });

  it("debounces search, requests pages of 50, and advances a page", async () => {
    const user = userEvent.setup();
    render(<InventoryView revision={0} uploads={[]} />);

    await waitFor(() =>
      expect(mocks.getInventory).toHaveBeenCalledWith(
        expect.objectContaining({ page: 1, page_size: 50 }),
      ),
    );
    const initialCalls = mocks.getInventory.mock.calls.length;
    fireEvent.change(screen.getByRole("searchbox", { name: "Search inventory" }), {
      target: { value: "orders" },
    });
    expect(mocks.getInventory).toHaveBeenCalledTimes(initialCalls);
    await waitFor(
      () =>
        expect(mocks.getInventory).toHaveBeenCalledWith(
          expect.objectContaining({ q: "orders", page: 1, page_size: 50 }),
        ),
      { timeout: 1000 },
    );

    await user.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() =>
      expect(mocks.getInventory).toHaveBeenCalledWith(
        expect.objectContaining({ q: "orders", page: 2, page_size: 50 }),
      ),
    );
  });

  it("shows an explicit external-reference count when present", async () => {
    mocks.getInventory.mockResolvedValue({
      items: [],
      page: 1,
      page_size: 50,
      total: 57,
      external_total: 1,
    });
    render(<InventoryView revision={0} uploads={[]} />);

    expect(await screen.findByText("57 objects · 1 external reference")).toBeInTheDocument();
  });

  it("shows only the object count when there are no external references", async () => {
    render(<InventoryView revision={0} uploads={[]} />);

    expect(await screen.findByText("120 objects")).toBeInTheDocument();
  });

  it("loads the graph through the mocked lazy component", async () => {
    const user = userEvent.setup();
    mocks.getInventory.mockResolvedValue({
      items: [{
        id: "object-1",
        upload_id: "upload-1",
        file: "tables.sql",
        type: "table",
        schema_name: "dw",
        name: "orders",
        qualified_name: "dw.orders",
        status: "parsed",
        start_line: 1,
        end_line: 3,
        upstream_count: 0,
        downstream_count: 0,
        source_preview: "CREATE TABLE dw.orders;",
        external: false,
      }],
      page: 1,
      page_size: 50,
      total: 1,
    });
    render(<InventoryView revision={0} uploads={[]} />);

    await user.click(await screen.findByText("dw.orders"));
    await user.click(await screen.findByRole("button", { name: "Show graph" }));
    expect(await screen.findByText("Mock dependency graph")).toBeInTheDocument();
  });
});
