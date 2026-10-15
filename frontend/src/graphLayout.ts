import type { CoseLayoutOptions } from "cytoscape";

const LAYOUT_NODE_COUNT_THRESHOLD = 12;
const MAX_LAYOUT_SPACING_SCALE = 3;

export function graphLayoutOptionsForNodeCount(nodeCount: number): CoseLayoutOptions {
  const spacingScale =
    1 + Math.min(MAX_LAYOUT_SPACING_SCALE - 1, Math.max(0, nodeCount - LAYOUT_NODE_COUNT_THRESHOLD) / 7.5);

  return {
    name: "cose",
    animate: false,
    padding: 40,
    nodeDimensionsIncludeLabels: true,
    spacingFactor: spacingScale,
    componentSpacing: 40 * spacingScale,
    idealEdgeLength: 32 * spacingScale,
    nodeRepulsion: 2048 * spacingScale ** 2,
    nodeOverlap: 4 * spacingScale,
  };
}
