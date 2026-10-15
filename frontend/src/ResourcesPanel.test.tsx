import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getResources: vi.fn(),
  updateResourceAvailability: vi.fn(),
}));

vi.mock("./api", () => mocks);

import { ResourcesPanel } from "./ResourcesPanel";

function tablesResource(count: number) {
  return {
    id: "tables",
    name: "Tables",
    description: "Source tables",
    object_types: ["table"],
    extraction_hint: "",
    tier: "mandatory" as const,
    available: true,
    count,
    auto_available: true,
    status: "Available",
  };
}

describe("ResourcesPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getResources.mockResolvedValue([tablesResource(7)]);
    mocks.updateResourceAvailability.mockResolvedValue({ id: "tables", available: true });
  });

  it("shows object counts and preselects resources available from discovery", async () => {
    render(<ResourcesPanel confirmed refreshKey={0} />);

    const checkbox = await screen.findByRole("checkbox", { name: "Tables available" });
    expect(checkbox).toBeChecked();
    expect(screen.getByText("7 objects")).toBeInTheDocument();
    expect(screen.getByText("Available")).toBeInTheDocument();

    fireEvent.click(checkbox);
    await waitFor(() =>
      expect(mocks.updateResourceAvailability).toHaveBeenCalledWith("tables", false),
    );
  });

  it("refreshes object counts when discovery updates the refresh key without a reload", async () => {
    mocks.getResources
      .mockResolvedValueOnce([tablesResource(2)])
      .mockResolvedValueOnce([tablesResource(9)]);
    const { rerender } = render(<ResourcesPanel confirmed refreshKey={0} />);

    expect(await screen.findByText("2 objects")).toBeInTheDocument();
    rerender(<ResourcesPanel confirmed refreshKey={1} />);

    expect(await screen.findByText("9 objects")).toBeInTheDocument();
    expect(mocks.getResources).toHaveBeenCalledTimes(2);
  });
});
