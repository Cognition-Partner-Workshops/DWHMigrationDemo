import { useEffect, useRef, useState } from "react";
import cytoscape from "cytoscape";
import type { Core, ElementDefinition } from "cytoscape";

import { getGraph, type GraphResult } from "./api";
import { graphLayoutOptionsForNodeCount } from "./graphLayout";
import { graphLabelFontSizeForZoom } from "./graphLabelSizing";

const TYPE_COLORS: Record<string, string> = {
  database: "#5473a8",
  external: "#a5adba",
  table: "#3d7fd6",
  view: "#7d63c6",
  materialized_view: "#6a55a8",
  procedure: "#e18a35",
  function: "#d45d7b",
  sequence: "#2b9a87",
  index: "#5c98a7",
  trigger: "#b2773c",
  package: "#627ab0",
  schema: "#468b64",
  type: "#a35e9b",
  other: "#77859a",
};

function colorFor(type: string): string {
  return TYPE_COLORS[type.toLowerCase()] ?? "#77859a";
}

function graphLabel(name: string): string {
  const pathParts = name.split("/");
  const lastPart = pathParts[pathParts.length - 1] ?? name;
  const label =
    pathParts.length > 1
      ? lastPart.replace(/\.[^.]+$/, "")
      : name.split(".").slice(-2).join(".");
  return label.length > 20 ? `${label.slice(0, 19)}…` : label;
}

function fitGraph(cy: Core) {
  cy.fit(undefined, 40);
  cy.zoom(Math.max(0.2, Math.min(1.5, cy.zoom())));
}

const NODE_LABEL_MAX_WIDTH = 130;

function updateNodeLabelSizing(cy: Core) {
  const zoom = cy.zoom();
  cy.nodes().style({
    "font-size": graphLabelFontSizeForZoom(zoom),
    "text-max-width": `${NODE_LABEL_MAX_WIDTH / zoom}px`,
  });
}

export function GraphView({
  objectId,
  onSelectObject,
}: {
  objectId: string;
  onSelectObject: (id: string) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const [depth, setDepth] = useState(1);
  const [graph, setGraph] = useState<GraphResult | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    void getGraph(objectId, depth)
      .then((result) => {
        if (!active) return;
        setGraph(result);
        setError("");
      })
      .catch((loadError: unknown) => {
        if (active) setError(loadError instanceof Error ? loadError.message : "Graph unavailable.");
      });
    return () => {
      active = false;
    };
  }, [depth, objectId]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || !graph) return;

    const elements: ElementDefinition[] = [
      ...graph.nodes.map((node) => ({
        data: {
          id: node.id,
          objectId: node.id,
          label: graphLabel(node.qualified_name),
          type: node.type,
          color: node.external ? "#a5adba" : colorFor(node.type),
          external: node.external,
        },
        classes: node.external ? "external" : "",
      })),
      ...graph.edges.map((edge, index) => ({
        data: {
          id: `edge-${index}-${edge.source_id}-${edge.target_id}-${edge.kind}`,
          source: edge.source_id,
          target: edge.target_id,
          kind: edge.kind,
        },
      })),
    ];
    const layout = graphLayoutOptionsForNodeCount(graph.nodes.length);

    const cy = cytoscape({
      container,
      elements,
      style: [
        {
          selector: "node",
          style: {
            label: "data(label)",
            "font-size": 10,
            "min-zoomed-font-size": 0,
            "text-wrap": "ellipsis",
            "text-max-width": "130px",
            "text-valign": "bottom",
            "text-halign": "center",
            "text-margin-y": 6,
            "text-background-color": "#ffffff",
            "text-background-opacity": 0.9,
            "text-background-padding": "2px",
            color: "#172b46",
            "background-color": "data(color)",
            width: 34,
            height: 34,
            "border-width": 1,
            "border-color": "#ffffff",
          },
        },
        {
          selector: "node.external",
          style: {
            "background-color": "#a5adba",
            "border-width": 2,
            "border-color": "#7e8998",
            "border-style": "dashed",
            color: "#394555",
          },
        },
        {
          selector: "node:selected",
          style: {
            "border-width": 4,
            "border-color": "#123f7a",
            "overlay-color": "#8bb8f0",
            "overlay-opacity": 0.16,
          },
        },
        {
          selector: "edge",
          style: {
            width: 1.5,
            "line-color": "#9aa9bc",
            "target-arrow-color": "#71849c",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
            label: "",
            "font-size": 8,
            "text-background-color": "#ffffff",
            "text-background-opacity": 0.9,
            "text-background-padding": "2px",
          },
        },
      ],
      layout,
      minZoom: 0.2,
      maxZoom: 1.5,
    });
    cyRef.current = cy;
    cy.getElementById(objectId).select();
    cy.on("tap", "node", (event) => {
      const selectedId = event.target.data("objectId") as string | undefined;
      if (selectedId && event.target.data("external") !== true) {
        onSelectObject(selectedId);
      }
    });
    cy.on("mouseover", "edge", (event) => {
      event.target.style("label", event.target.data("kind") as string);
    });
    cy.on("mouseout", "edge", (event) => {
      event.target.style("label", "");
    });
    cy.on("zoom", () => updateNodeLabelSizing(cy));
    fitGraph(cy);
    updateNodeLabelSizing(cy);

    return () => {
      cy.destroy();
      if (cyRef.current === cy) cyRef.current = null;
    };
  }, [graph, objectId, onSelectObject]);

  function resetGraph() {
    if (depth !== 1) {
      setDepth(1);
      return;
    }
    const cy = cyRef.current;
    if (cy) {
      cy.layout(graphLayoutOptionsForNodeCount(graph?.nodes.length ?? 0)).run();
      fitGraph(cy);
    }
  }

  return (
    <div className="graph-view">
      <div className="graph-toolbar">
        <label>
          Depth
          <select
            aria-label="Graph depth"
            onChange={(event) => setDepth(Number(event.target.value))}
            value={depth}
          >
            {[1, 2, 3].map((level) => <option key={level} value={level}>{level}</option>)}
          </select>
        </label>
        <button
          onClick={() => {
            const cy = cyRef.current;
            if (cy) fitGraph(cy);
          }}
          type="button"
        >
          Fit
        </button>
        <button onClick={resetGraph} type="button">Reset</button>
      </div>
      {graph?.truncated && (
        <p className="graph-truncation" role="status">Graph truncated at 300 objects.</p>
      )}
      {error && <p className="inventory-error" role="alert">{error}</p>}
      <div aria-label="Dependency graph" className="graph-canvas" ref={containerRef} />
      <div aria-label="Object type legend" className="graph-legend">
        {[...new Set((graph?.nodes ?? []).map((node) => node.external ? "external" : node.type.toLowerCase()))]
          .map((type) => (
            <span key={type}>
              <i
                className={type === "external" ? "external-legend" : undefined}
                style={{ backgroundColor: colorFor(type) }}
              />
              {type}
            </span>
          ))}
      </div>
    </div>
  );
}
