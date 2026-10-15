import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  cyFactory: vi.fn(),
  getGraph: vi.fn(),
  cy: {
    destroy: vi.fn(),
    fit: vi.fn(),
    getElementById: vi.fn(),
    layout: vi.fn(),
    on: vi.fn(),
    zoom: vi.fn(),
  },
  node: { select: vi.fn() },
  layout: { run: vi.fn() },
}));

vi.mock("cytoscape", () => ({ default: mocks.cyFactory }));
vi.mock("./api", () => ({ getGraph: mocks.getGraph }));

import { GraphView } from "./GraphView";

describe("GraphView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.cy.getElementById.mockReturnValue(mocks.node);
    mocks.cy.layout.mockReturnValue(mocks.layout);
    mocks.cy.zoom.mockImplementation((level?: number) => level ?? 2);
    mocks.cyFactory.mockReturnValue(mocks.cy);
    mocks.getGraph.mockResolvedValue({
      nodes: [{
        id: "object-1",
        upload_id: "upload-1",
        file: "tables.sql",
        type: "table",
        schema_name: "dw",
        name: "orders",
        qualified_name: "root/tables/orders.sql",
        status: "parsed",
        start_line: 1,
        end_line: 3,
        source_preview: "",
        external: false,
      }],
      edges: [],
      truncated: true,
    });
  });

  it("mounts Cytoscape with the bundled graph and destroys it on unmount", async () => {
    const { unmount } = render(<GraphView objectId="object-1" onSelectObject={vi.fn()} />);
    expect(await screen.findByText("Graph truncated at 300 objects.")).toBeInTheDocument();
    await waitFor(() => expect(mocks.cyFactory).toHaveBeenCalledOnce());
    const graphOptions = mocks.cyFactory.mock.calls[0][0];
    expect(graphOptions.layout.name).toBe("cose");
    expect(graphOptions.minZoom).toBe(0.2);
    expect(graphOptions.maxZoom).toBe(1.5);
    expect(graphOptions.elements[0].data.label).toBe("orders");
    expect(graphOptions.style[0].style["font-size"]).toBe(11);
    expect(graphOptions.style[0].style["text-valign"]).toBe("bottom");
    expect(mocks.cy.fit).toHaveBeenCalledWith(undefined, 40);
    expect(mocks.cy.zoom).toHaveBeenCalledWith(1.5);
    expect(mocks.node.select).toHaveBeenCalledOnce();

    unmount();
    expect(mocks.cy.destroy).toHaveBeenCalledOnce();
  });

  it("shows each type in the current graph once", async () => {
    const tableNode = {
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
      source_preview: "",
      external: false,
    };
    mocks.getGraph.mockResolvedValue({
      nodes: [
        tableNode,
        { ...tableNode, id: "object-2", name: "payments", qualified_name: "dw.payments" },
        { ...tableNode, id: "external-1", external: true, qualified_name: "legacy.archive" },
      ],
      edges: [],
      truncated: false,
    });
    render(<GraphView objectId="object-1" onSelectObject={vi.fn()} />);

    const legend = await screen.findByLabelText("Object type legend");
    expect(Array.from(legend.querySelectorAll("span"), (item) => item.textContent)).toEqual([
      "table",
      "external",
    ]);
  });
});
