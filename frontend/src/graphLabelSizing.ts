export function graphLabelFontSizeForZoom(zoom: number): number {
  return Math.min(24, Math.max(10, 10 / zoom));
}
