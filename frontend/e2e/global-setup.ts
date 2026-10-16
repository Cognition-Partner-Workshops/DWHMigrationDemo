import { rmSync } from "node:fs";
import { mkdir } from "node:fs/promises";
import { dirname } from "node:path";
import process from "node:process";

import { request, type FullConfig } from "@playwright/test";

export default async function globalSetup(_config: FullConfig): Promise<() => void> {
  const token = process.env.MIGRAAI_E2E_DEV_TOKEN;
  const runDir = process.env.MIGRAAI_E2E_RUN_DIR;
  if (!token || !runDir) {
    throw new Error("Playwright auth configuration is incomplete.");
  }

  for (const project of _config.projects) {
    const baseURL = project.use.baseURL;
    const authStatePath = project.use.storageState;
    if (typeof baseURL !== "string" || typeof authStatePath !== "string") {
      throw new Error(`Playwright auth configuration is incomplete for ${project.name}.`);
    }
    await mkdir(dirname(authStatePath), { recursive: true });
    const context = await request.newContext({ baseURL });
    try {
      const response = await context.get("/api/auth/launch", { params: { token } });
      if (!response.ok()) {
        throw new Error(`Development launch authentication returned HTTP ${response.status()}.`);
      }
      await context.storageState({ path: authStatePath });
    } finally {
      await context.dispose();
    }
  }

  return () => {
    rmSync(runDir, { recursive: true, force: true });
  };
}
