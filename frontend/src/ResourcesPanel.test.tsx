import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getResources: vi.fn(),
  updateResourceAvailability: vi.fn(),
}));

vi.mock("./api", () => mocks);

import { ResourcesPanel } from "./ResourcesPanel";

describe("ResourcesPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getResources.mockResolvedValue([
      {
        id: "tables",
        name: "Tables",
        description: "Source tables",
        object_types: ["table"],
        extraction_hint: "",
        tier: "mandatory",
        available: true,
        count: 7,
        auto_available: true,
        status: "Available",
      },
    ]);
    mocks.updateResourceAvailability.mockResolvedValue({ id: "tables", available: true });
  });

  it("shows object counts and preselects resources available from discovery", async () => {
    render(<ResourcesPanel confirmed />);

    const checkbox = await screen.findByRole("checkbox", { name: "Tables available" });
    expect(checkbox).toBeChecked();
    expect(screen.getByText("7 objects")).toBeInTheDocument();
    expect(screen.getByText("Available")).toBeInTheDocument();

    fireEvent.click(checkbox);
    await waitFor(() =>
      expect(mocks.updateResourceAvailability).toHaveBeenCalledWith("tables", false),
    );
  });
});
