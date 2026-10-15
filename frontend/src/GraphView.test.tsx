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
    nodes: vi.fn(),
    on: vi.fn(),
    zoom: vi.fn(),
  },
  nodeCollection: { style: vi.fn() },
  node: {
    nonempty: vi.fn(() => true),
    position: vi.fn(() => ({ x: 100, y: 200 })),
    select: vi.fn(),
  },
  layout: { run: vi.fn() },
}));

vi.mock("cytoscape", () => ({ default: mocks.cyFactory }));
vi.mock("./api", () => ({ getGraph: mocks.getGraph }));

import { GraphView } from "./GraphView";
import { graphLayoutOptionsForNodeCount } from "./graphLayout";
import { graphLabelFontSizeForZoom } from "./graphLabelSizing";

describe("GraphView", () => {
  let currentZoom = 2;

  beforeEach(() => {
    vi.clearAllMocks();
    currentZoom = 2;
    mocks.cy.getElementById.mockReturnValue(mocks.node);
    mocks.cy.layout.mockReturnValue(mocks.layout);
    mocks.cy.nodes.mockReturnValue(mocks.nodeCollection);
    mocks.cy.zoom.mockImplementation((value?: number) => {
      if (typeof value === "number") currentZoom = value;
      return currentZoom;
    });
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
    expect(graphOptions.style[0].style["font-size"]).toBe(10);
    expect(graphOptions.style[0].style["min-zoomed-font-size"]).toBe(0);
    expect(graphOptions.style[0].style["text-wrap"]).toBe("ellipsis");
    expect(graphOptions.style[0].style["text-valign"]).toBe("bottom");
    expect(graphOptions.layout).toEqual(graphLayoutOptionsForNodeCount(1));
    expect(mocks.cy.fit).toHaveBeenCalledWith(undefined, 40);
    expect(mocks.cy.zoom).toHaveBeenCalledWith(1.5);
    expect(mocks.nodeCollection.style).toHaveBeenLastCalledWith({
      "font-size": 10,
      "text-max-width": `${130 / 1.5}px`,
    });
    expect(mocks.node.select).toHaveBeenCalledOnce();

    unmount();
    expect(mocks.cy.destroy).toHaveBeenCalledOnce();
  });

  it("preserves a low automatic fit and sizes labels for that zoom", async () => {
    currentZoom = 0.5;
    render(<GraphView objectId="object-1" onSelectObject={vi.fn()} />);

    await waitFor(() => expect(mocks.cy.zoom).toHaveBeenCalledWith(0.5));
    expect(mocks.cy.zoom).not.toHaveBeenCalledWith(
      expect.objectContaining({ level: 0.7 }),
    );
    expect(mocks.nodeCollection.style).toHaveBeenLastCalledWith({
      "font-size": 20,
      "text-max-width": "260px",
    });
  });

  it.each([
    [0.2, 24],
    [0.5, 20],
    [1, 10],
    [2, 10],
  ])("clamps label size at zoom %s", (zoom, expected) => {
    expect(graphLabelFontSizeForZoom(zoom)).toBe(expected);
  });

  it("increases cose spacing for graphs larger than twelve nodes", async () => {
    const node = mocks.getGraph.mock.results[0]?.value?.nodes[0] ?? {
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
      nodes: Array.from({ length: 27 }, (_, index) => ({
        ...node,
        id: `object-${index + 1}`,
        name: `orders_${index + 1}`,
        qualified_name: `dw.orders_${index + 1}`,
      })),
      edges: [],
      truncated: false,
    });
    render(<GraphView objectId="object-1" onSelectObject={vi.fn()} />);

    await waitFor(() => expect(mocks.cyFactory).toHaveBeenCalledOnce());
    expect(mocks.cyFactory.mock.calls[0][0].layout).toMatchObject({
      nodeDimensionsIncludeLabels: true,
      spacingFactor: 3,
      componentSpacing: 120,
      idealEdgeLength: 96,
      nodeRepulsion: 18432,
      nodeOverlap: 12,
    });
  });

  it("selects a tapped node so its detail can show the full name", async () => {
    const onSelectObject = vi.fn();
    render(<GraphView objectId="object-1" onSelectObject={onSelectObject} />);
    await waitFor(() => expect(mocks.cyFactory).toHaveBeenCalledOnce());

    const tapHandler = mocks.cy.on.mock.calls.find(
      ([event, selector]) => event === "tap" && selector === "node",
    )?.[2];
    expect(tapHandler).toBeDefined();
    tapHandler?.({
      target: {
        data: (key: string) => (key === "objectId" ? "object-1" : false),
      },
    });

    expect(onSelectObject).toHaveBeenCalledWith("object-1");
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
