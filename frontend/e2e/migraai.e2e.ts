import { Buffer } from "node:buffer";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Page } from "@playwright/test";

const frontendDir = dirname(dirname(fileURLToPath(import.meta.url)));
const teradataPack = resolve(frontendDir, "../testpacks/teradata");

type ZipEntry = { name: string; content: Buffer };

function crc32(content: Buffer): number {
  let crc = 0xffffffff;
  for (const byte of content) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function makeZip(entries: ZipEntry[]): Buffer {
  const localParts: Buffer[] = [];
  const centralParts: Buffer[] = [];
  let localOffset = 0;
  let centralSize = 0;

  for (const entry of entries) {
    const name = Buffer.from(entry.name, "utf8");
    const checksum = crc32(entry.content);
    const localHeader = Buffer.alloc(30);
    localHeader.writeUInt32LE(0x04034b50, 0);
    localHeader.writeUInt16LE(20, 4);
    localHeader.writeUInt16LE(0, 6);
    localHeader.writeUInt16LE(0, 8);
    localHeader.writeUInt16LE(0, 10);
    localHeader.writeUInt16LE(0x21, 12);
    localHeader.writeUInt32LE(checksum, 14);
    localHeader.writeUInt32LE(entry.content.length, 18);
    localHeader.writeUInt32LE(entry.content.length, 22);
    localHeader.writeUInt16LE(name.length, 26);
    localHeader.writeUInt16LE(0, 28);
    localParts.push(localHeader, name, entry.content);

    const centralHeader = Buffer.alloc(46);
    centralHeader.writeUInt32LE(0x02014b50, 0);
    centralHeader.writeUInt16LE(20, 4);
    centralHeader.writeUInt16LE(20, 6);
    centralHeader.writeUInt16LE(0, 8);
    centralHeader.writeUInt16LE(0, 10);
    centralHeader.writeUInt16LE(0, 12);
    centralHeader.writeUInt16LE(0x21, 14);
    centralHeader.writeUInt32LE(checksum, 16);
    centralHeader.writeUInt32LE(entry.content.length, 20);
    centralHeader.writeUInt32LE(entry.content.length, 24);
    centralHeader.writeUInt16LE(name.length, 28);
    centralHeader.writeUInt16LE(0, 30);
    centralHeader.writeUInt16LE(0, 32);
    centralHeader.writeUInt16LE(0, 34);
    centralHeader.writeUInt16LE(0, 36);
    centralHeader.writeUInt32LE(0, 38);
    centralHeader.writeUInt32LE(localOffset, 42);
    centralParts.push(centralHeader, name);

    localOffset += localHeader.length + name.length + entry.content.length;
    centralSize += centralHeader.length + name.length;
  }

  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(0, 4);
  end.writeUInt16LE(0, 6);
  end.writeUInt16LE(entries.length, 8);
  end.writeUInt16LE(entries.length, 10);
  end.writeUInt32LE(centralSize, 12);
  end.writeUInt32LE(localOffset, 16);
  end.writeUInt16LE(0, 20);
  return Buffer.concat([...localParts, ...centralParts, end]);
}

function zipEntryNames(content: Buffer): string[] {
  let endOffset = -1;
  for (let offset = content.length - 22; offset >= Math.max(0, content.length - 65_557); offset -= 1) {
    if (content.readUInt32LE(offset) === 0x06054b50) {
      endOffset = offset;
      break;
    }
  }
  if (endOffset < 0) {
    throw new Error("Downloaded file is not a ZIP archive.");
  }
  const count = content.readUInt16LE(endOffset + 10);
  let offset = content.readUInt32LE(endOffset + 16);
  const names: string[] = [];
  for (let index = 0; index < count; index += 1) {
    if (content.readUInt32LE(offset) !== 0x02014b50) {
      throw new Error("ZIP central directory is invalid.");
    }
    const nameLength = content.readUInt16LE(offset + 28);
    const extraLength = content.readUInt16LE(offset + 30);
    const commentLength = content.readUInt16LE(offset + 32);
    names.push(content.toString("utf8", offset + 46, offset + 46 + nameLength));
    offset += 46 + nameLength + extraLength + commentLength;
  }
  return names.sort();
}

function teradataArchive(): Buffer {
  const entries = readdirSync(teradataPack)
    .filter((name) => name.toLowerCase().endsWith(".sql"))
    .sort()
    .map((name) => ({
      name,
      content: readFileSync(resolve(teradataPack, name)),
    }));
  return makeZip(entries);
}

async function uploadZip(
  page: Page,
  name: string,
  buffer: Buffer,
): Promise<void> {
  await page.getByLabel("Choose files to upload").setInputFiles({
    name,
    mimeType: "application/zip",
    buffer,
  });
  await page.getByRole("button", { name: "Upload Files" }).click();
}

test("mock-only migration workflow", async ({ page }, testInfo) => {
  const progressSnapshots: string[] = [];
  page.on("response", async (response) => {
    if (!response.url().includes("/api/convert")) {
      return;
    }
    try {
      const body = await response.json();
      if (Array.isArray(body.batches)) {
        progressSnapshots.push(
          body.batches
            .map((batch: { voters?: Record<string, string> }) => JSON.stringify(batch.voters ?? {}))
            .join("\n"),
        );
      }
    } catch {
      return;
    }
  });

  await page.goto("/");
  await expect(page.getByLabel("Source platform")).toBeVisible();
  await page.getByLabel("Source platform").selectOption("teradata");
  await page.getByLabel("Target platform").selectOption("snowflake");
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByText(/Local draft/).first()).toBeVisible();
  await expect(page.getByText("Your data stays on this machine")).toBeVisible();

  await page.getByLabel("Choose files to upload").setInputFiles({
    name: "unsafe.zip",
    mimeType: "application/zip",
    buffer: makeZip([{ name: "../unsafe.sql", content: Buffer.from("CREATE TABLE unsafe (id INT);") }]),
  });
  const unsafeUploadResponse = page.waitForResponse(
    (response) =>
      response.url().includes("/api/uploads") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Upload Files" }).click();
  const unsafeResponse = await unsafeUploadResponse;
  if (unsafeResponse.ok()) {
    const payload = (await unsafeResponse.json()) as {
      uploads?: { rel_path: string; status: string }[];
    };
    const rejected = payload.uploads?.filter((upload) => upload.status === "rejected") ?? [];
    expect(rejected.length).toBeGreaterThan(0);
    const rejectedUpload = rejected[0];
    if (!rejectedUpload) {
      throw new Error("The unsafe ZIP was not rejected.");
    }
    await expect(
      page.getByRole("row").filter({ hasText: rejectedUpload.rel_path }).getByText("rejected"),
    ).toBeVisible();
    page.once("dialog", (dialog) => void dialog.accept());
    await page.getByRole("button", { name: `Delete ${rejectedUpload.rel_path}` }).click();
  } else {
    await expect(page.getByRole("alert")).toContainText("Upload failed");
    await page.getByRole("button", { name: "Remove unsafe.zip" }).click();
  }

  await uploadZip(page, "teradata-testpack.zip", teradataArchive());
  await expect(page.locator(".upload-summary")).toContainText("Discovery complete", {
    timeout: 30_000,
  });
  await expect
    .poll(
      () =>
        page.evaluate(async () => {
          const response = await fetch("/api/discovery/status");
          return ((await response.json()) as { state: string }).state;
        }),
      { timeout: 30_000 },
    )
    .toBe("done");
  await page.getByRole("tab", { name: /Inventory/ }).click();
  const inventory = page.locator(".inventory-view");
  await inventory.getByLabel("Search inventory").fill("sales.customer");
  const customerRow = inventory.getByRole("button", { name: /sales\.customer/ }).first();
  await expect(customerRow).toBeVisible();
  await inventory.getByLabel("Filter by type").selectOption("table");
  await expect(customerRow).toBeVisible();
  await customerRow.click();

  const graphControl = page.getByRole("button", { name: /graph/i });
  await graphControl.click();
  await expect(page.getByLabel("Dependency graph")).toBeVisible();
  await expect
    .poll(() => page.getByLabel("Dependency graph").locator("canvas").count())
    .toBeGreaterThan(0);

  await page.getByRole("button", { name: "Convert Items" }).click();
  const consentDialog = page.getByRole("dialog", { name: "Send code to Devin?" });
  await expect(consentDialog).toBeVisible();
  const sendButton = consentDialog.getByRole("button", { name: "Send to Devin" });
  await expect(sendButton).toBeDisabled();
  await consentDialog.getByLabel("I understand that this code leaves this computer").check();
  await expect(sendButton).toBeEnabled();
  await sendButton.click();

  await expect(page.getByLabel("Conversion progress")).toBeVisible();
  const firstBatch = page.locator(".conversion-batch").first();
  await expect(firstBatch).toContainText("A: running", { timeout: 20_000 });
  await expect(firstBatch).toContainText("B: running", { timeout: 20_000 });
  await expect(firstBatch).toContainText("C: running", { timeout: 20_000 });
  await expect(page.getByText("Conversion done")).toBeVisible({ timeout: 120_000 });
  expect(progressSnapshots.some((snapshot) => snapshot.includes('"A":"running"'))).toBeTruthy();
  expect(progressSnapshots.some((snapshot) => snapshot.includes('"B":"running"'))).toBeTruthy();
  expect(progressSnapshots.some((snapshot) => snapshot.includes('"C":"running"'))).toBeTruthy();

  const results = page.getByLabel("Conversion results");
  const objectButton = results.getByRole("button", { name: /sales\.account/ }).first();
  await expect(objectButton).toBeVisible();
  const resultRow = objectButton.locator("xpath=ancestor::tr");
  await expect(resultRow.locator(".check-chip")).toHaveCount(7);
  await objectButton.click();
  const detail = page.getByLabel("Conversion detail");
  await expect(detail).toBeVisible();
  const candidateTabs = detail.getByRole("tab");
  await expect(candidateTabs).toHaveCount(4);
  for (const candidate of ["A", "B", "C"]) {
    await detail.getByRole("tab", { name: candidate, exact: true }).click();
    const copyButton = detail.locator(".copy-file").first();
    await expect(copyButton).toBeVisible();
    await copyButton.click();
    await expect(copyButton).toHaveText("Copied");
  }

  await results.getByRole("checkbox").first().check();
  const reconvertButton = page.getByRole("button", { name: "Re-convert selected (1)" });
  await expect(reconvertButton).toBeEnabled();
  await reconvertButton.click();
  const reconvertDialog = page.getByRole("dialog", { name: "Send code to Devin?" });
  const reconvertSend = reconvertDialog.getByRole("button", { name: "Send to Devin" });
  await expect(reconvertSend).toBeDisabled();
  await reconvertDialog.getByLabel("I understand that this code leaves this computer").check();
  const reconvertResponse = page.waitForResponse(
    (response) => response.url().endsWith("/api/convert") && response.request().method() === "POST",
  );
  await reconvertSend.click();
  const reconvertJobId = (await (await reconvertResponse).json()).job_id as string;
  const reconvertStatus = () =>
    page.evaluate(async (jobId) => {
      const response = await fetch(`/api/convert/jobs/${jobId}`);
      return await response.json() as { state: string; object_count: number };
    }, reconvertJobId);
  await expect.poll(
    async () => (await reconvertStatus()).state,
    { timeout: 90_000 },
  ).toBe("done");
  await expect.poll(async () => (await reconvertStatus()).object_count).toBe(1);

  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("button", { name: /Download Converted Files/ }).click(),
  ]);
  expect(download.suggestedFilename()).toBe("teradata-testpack-converted.zip");
  const downloadPath = testInfo.outputPath(download.suggestedFilename());
  await download.saveAs(downloadPath);
  const entries = zipEntryNames(readFileSync(downloadPath));
  expect(entries).toContain("teradata-testpack-converted/manifest.json");
  expect(entries.some((entry) => entry.startsWith("teradata-testpack-converted/converted/"))).toBe(
    true,
  );

  for (const [format, contentType] of [
    ["md", "text/markdown"],
    ["pdf", "application/pdf"],
  ] as const) {
    const responsePromise = page.waitForResponse(
      (response) =>
        response.url().includes(`/api/report.${format}`) && response.request().method() === "GET",
    );
    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("link", { name: `Report (${format.toUpperCase()})` }).click();
    const response = await responsePromise;
    await downloadPromise;
    expect(response.status()).toBe(200);
    expect(response.headers()["content-type"]).toContain(contentType);
  }

  await page.getByRole("button", { name: "Convert Items" }).click();
  const secondDialog = page.getByRole("dialog", { name: "Send code to Devin?" });
  await secondDialog.getByLabel("I understand that this code leaves this computer").check();
  await secondDialog.getByRole("button", { name: "Send to Devin" }).click();
  await expect(page.getByLabel("Conversion progress")).toBeVisible();
  await expect(page.locator(".conversion-batch").nth(1)).toContainText(/A: running|B: running|C: running/, {
    timeout: 40_000,
  });
  await page.reload();
  await expect(page.getByLabel("Conversion progress")).toBeVisible();
  await expect(page.getByText("Conversion running")).toBeVisible();
  const cancelButton = page.getByRole("button", { name: "Cancel", exact: true });
  await expect
    .poll(async () => {
      const counts = await page.locator(".conversion-counts strong").allTextContents();
      return Number(counts[0] ?? 0) + Number(counts[2] ?? 0);
    }, { timeout: 90_000 })
    .toBeGreaterThan(0);
  await expect(cancelButton).toBeVisible();
  await cancelButton.click();
  const cancelDialog = page.getByRole("alertdialog", { name: "Confirm cancellation" });
  await expect(cancelDialog).toBeVisible();
  await cancelDialog.getByRole("button", { name: "Cancel conversion" }).click();
  await expect(page.getByText("Conversion cancelled")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("button", { name: /Download Converted Files/ })).toBeEnabled();

  const pageOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
  );
  expect(pageOverflow).toBe(false);
});
