import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";

const settingsResponse = {
  devin_pat: { configured: true, masked: "••••abcd" },
  devin_org_id: "org-demo",
  devin_api_base_url: "https://api.devin.ai",
  confidence_threshold: 0.85,
  max_concurrent_sessions: 4,
  acu_cap_per_run: null,
};

function mockApi(devinConfigured = true) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/api/health")) {
      return Response.json({
        status: "ready",
        mode: "local",
        version: "0.1.0",
        devin_configured: devinConfigured,
      });
    }
    if (path.endsWith("/api/platforms")) {
      return Response.json({
        sources: [
          { id: "teradata", name: "Teradata" },
          { id: "snowflake", name: "Snowflake" },
        ],
        targets: [
          { id: "databricks", name: "Databricks" },
          { id: "snowflake", name: "Snowflake" },
        ],
      });
    }
    if (path.endsWith("/api/settings")) {
      return Response.json(settingsResponse);
    }
    if (path.endsWith("/api/session/confirm")) {
      return Response.json({
        session_id: "session-1",
        source: "teradata",
        target: "databricks",
      });
    }
    return Response.json(settingsResponse);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function confirmSystems(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByRole("option", { name: "Teradata" });
  await screen.findByRole("option", { name: "Databricks" });
  await user.selectOptions(await screen.findByLabelText("Source platform"), "teradata");
  await user.selectOptions(screen.getByLabelText("Target platform"), "databricks");
  await user.click(screen.getByRole("button", { name: /confirm/i }));
}

describe("MigraAI foundation UI", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    window.history.replaceState(null, "", "/");
  });

  it("enables Confirm only when source and target are selected and different", async () => {
    const user = userEvent.setup();
    mockApi();
    render(<App />);

    const confirm = await screen.findByRole("button", { name: /confirm/i });
    expect(confirm).toBeDisabled();
    await screen.findByRole("option", { name: "Databricks" });
    await user.selectOptions(screen.getByLabelText("Source platform"), "snowflake");
    await user.selectOptions(screen.getByLabelText("Target platform"), "snowflake");
    expect(confirm).toBeDisabled();
    await user.selectOptions(screen.getByLabelText("Target platform"), "databricks");
    expect(confirm).toBeEnabled();
  });

  it("keeps Upload disabled until confirmation and Convert disabled until upload", async () => {
    const user = userEvent.setup();
    mockApi();
    render(<App />);

    const upload = await screen.findByRole("button", { name: /upload files/i });
    const convert = screen.getByRole("button", { name: /convert items/i });
    const download = screen.getByRole("button", { name: /download converted files/i });
    expect(
      screen.getByText("1. Browse or drop files · 2. Upload Files · 3. Convert Items"),
    ).toBeInTheDocument();
    expect(upload).toHaveAttribute("title", "Confirm source and target, then add files");
    expect(convert).toHaveAttribute("title", "Upload files first");
    expect(download).toHaveAttribute(
      "title",
      "Available once conversion is implemented in a later phase",
    );
    expect(upload).toBeDisabled();
    expect(convert).toBeDisabled();
    expect(download).toBeDisabled();

    const file = new File(["select 1"], "tables.sql", { type: "text/plain" });
    fireEvent.change(screen.getByLabelText("Choose migration files"), {
      target: { files: [file] },
    });
    expect(upload).toBeDisabled();
    await confirmSystems(user);
    expect(upload).toBeEnabled();
    expect(convert).toBeDisabled();

    await user.click(upload);
    expect(convert).toBeEnabled();
    expect(download).toBeDisabled();
    await user.click(convert);
    expect(download).toBeDisabled();
    expect(
      await screen.findByText("Preview complete — no conversion was run."),
    ).toBeInTheDocument();
  });

  it("shows the PAT as masked settings metadata, not as an input value", async () => {
    const user = userEvent.setup();
    mockApi();
    render(<App />);

    await user.click(await screen.findByRole("button", { name: /open settings/i }));
    await screen.findByText("Current token: ••••abcd");
    const token = await screen.findByLabelText(/Devin personal access token/);
    expect(token).toHaveAttribute("type", "password");
    expect(token).toHaveValue("");
    expect(token).toHaveAttribute("placeholder", "••••abcd");
    expect(screen.getByText("Current token: ••••abcd")).toBeInTheDocument();
  });

  it("shows local data handling text and the upload cleanup note", async () => {
    mockApi(false);
    const { container } = render(<App />);

    expect(await screen.findByText("Your data stays on this machine")).toBeInTheDocument();
    expect(screen.getByText("Uploads are deleted on exit")).toBeInTheDocument();
    expect(container.querySelector(".egress-banner svg")).toBeInTheDocument();
  });

  it("shows Devin cloud egress when Devin is configured", async () => {
    mockApi(true);
    render(<App />);

    expect(await screen.findByText("Conversions are sent to Devin cloud")).toBeInTheDocument();
  });

  it("sends same-origin credentials and CSRF token on mutations", async () => {
    const fetchMock = mockApi();
    document.cookie = "migraai_csrf=csrf-test; path=/";
    render(<App />);
    await waitFor(() => expect(screen.getByLabelText("Source platform")).toBeInTheDocument());

    const user = userEvent.setup();
    await confirmSystems(user);
    const mutationCall = fetchMock.mock.calls.find(([input]) =>
      String(input).endsWith("/api/session/confirm"),
    );
    expect(mutationCall).toBeDefined();
    const options = mutationCall?.[1];
    expect(options).toMatchObject({
      method: "POST",
      credentials: "same-origin",
    });
    expect(new Headers(options?.headers).get("X-MigraAI-CSRF")).toBe("csrf-test");
  });
});
