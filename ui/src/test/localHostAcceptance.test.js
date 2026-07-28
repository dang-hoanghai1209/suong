// @vitest-environment node

import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it } from "vitest";

import {
  BackendProductionRepository,
  defineNativeFetchClient,
} from "../api/productionClient";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const children = new Set();
const temporaryRoots = new Set();

async function waitForClose(child, timeoutMs) {
  if (child.exitCode !== null) {
    return true;
  }
  return new Promise((resolve) => {
    const timeout = setTimeout(() => resolve(false), timeoutMs);
    child.once("close", () => {
      clearTimeout(timeout);
      resolve(true);
    });
  });
}

afterEach(async () => {
  for (const child of children) {
    if (!(await waitForClose(child, 3_000))) {
      child.kill();
      await waitForClose(child, 2_000);
    }
  }
  children.clear();
  for (const root of temporaryRoots) {
    rmSync(root, { recursive: true, force: true });
  }
  temporaryRoots.clear();
});

async function startHost() {
  const uiRoot = mkdtempSync(path.join(tmpdir(), "tella-plan-only-host-"));
  temporaryRoots.add(uiRoot);
  writeFileSync(path.join(uiRoot, "index.html"), "<!doctype html><title>PLAN_ONLY</title>");
  const script = [
    "from pathlib import Path",
    "import os",
    "from threading import Timer",
    "from tella.topic_production.local_host import create_plan_only_http_server",
    "server = create_plan_only_http_server(",
    "    port=0,",
    "    ui_root=Path(os.environ['TELLA_TEST_UI_ROOT']),",
    "    allow_ephemeral_port=True,",
    ")",
    "print(server.server_address[1], flush=True)",
    "stop = Timer(3, server.shutdown)",
    "stop.start()",
    "try:",
    "    server.serve_forever(poll_interval=0.01)",
    "finally:",
    "    stop.cancel()",
    "    server.server_close()",
  ].join("\n");
  const child = spawn("uv", ["run", "--offline", "python", "-c", script], {
    cwd: repoRoot,
    env: {
      ...process.env,
      PYTHONPATH: repoRoot,
      TELLA_TEST_UI_ROOT: uiRoot,
    },
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });
  children.add(child);
  const port = await new Promise((resolve, reject) => {
    let stdout = "";
    let stderr = "";
    const timeout = setTimeout(() => reject(new Error("local host startup timed out")), 8_000);
    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
      const line = stdout.split(/\r?\n/, 1)[0];
      if (/^\d+$/.test(line)) {
        clearTimeout(timeout);
        resolve(Number(line));
      }
    });
    child.once("error", (error) => {
      clearTimeout(timeout);
      reject(error);
    });
    child.once("exit", (code) => {
      if (!/^\d+/.test(stdout)) {
        clearTimeout(timeout);
        reject(new Error(`local host exited (${code}): ${stderr}`));
      }
    });
  });
  return { child, port };
}

describe("short-lived local host acceptance", () => {
  it(
    "runs the TypeScript client through the real loopback host and application",
    async () => {
      const { child, port } = await startHost();
      const localFetch = (input, init) => {
        if (typeof input !== "string" || !input.startsWith("/api/v1/plan-only/")) {
          throw new Error("unexpected non-local contract request");
        }
        return fetch(`http://127.0.0.1:${port}${input}`, init);
      };
      const repository = new BackendProductionRepository(
        defineNativeFetchClient(localFetch),
      );

      const result = await repository.createPlanOnlyRun({
        schema_version: 1,
        input_mode: "TOPIC",
        source_content: "Learning to make room for uncertainty",
        language: "en",
        character_scope: "recurring_female",
        requested_scene_count: 8,
      });
      const runId = result.run?.run_id;

      expect(runId).toMatch(/^plan-[0-9a-f]{20}$/);
      await expect(repository.getRun(runId)).resolves.toMatchObject({
        run_id: runId,
        execution_mode: "PLAN_ONLY",
        render_readiness: { ready: false },
      });
      expect(await waitForClose(child, 5_000)).toBe(true);
    },
    15_000,
  );
});
