// @vitest-environment node

import { execFileSync } from "node:child_process";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { BackendProductionRepository } from "../api/productionClient";

const pythonProgram = String.raw`
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from tests.test_plan_only_execution_enablement import _approved_application, _create

with TemporaryDirectory() as directory:
    application, _, run_id, _, _, _, _, report = _approved_application(Path(directory))
    package = _create(application, run_id, report)["package"]
    print(json.dumps({
        "run_id": run_id,
        "access": application.get_execution_enablement_access(run_id),
        "package": package,
        "history": application.get_execution_package_history(run_id),
    }, ensure_ascii=True, sort_keys=True))
`;

function backendPayload() {
  const repositoryRoot = path.resolve(process.cwd(), "..");
  const output = execFileSync("uv", ["run", "python", "-c", pythonProgram], {
    cwd: repositoryRoot,
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: repositoryRoot },
  });
  return JSON.parse(output);
}

describe("execution-enablement Python-to-TypeScript V1 contract drift", () => {
  it("validates package authority, history, fingerprint, and false capabilities", async () => {
    const payload = backendPayload();
    const repository = new BackendProductionRepository({
      async request(requestPath) {
        if (requestPath.endsWith("/execution-enablement/access")) {
          return { status: 200, payload: payload.access };
        }
        if (requestPath.endsWith("/execution-enablement/history")) {
          return { status: 200, payload: payload.history };
        }
        return { status: 404, payload: null };
      },
    });
    const access = await repository.getExecutionEnablementAccess(payload.run_id);
    const history = await repository.getExecutionPackageHistory(payload.run_id);
    expect(access?.package).toEqual(payload.package);
    expect(history?.packages).toEqual([payload.package]);
    expect(access?.package?.package_source_authority_sha256).toMatch(/^[0-9a-f]{64}$/);
    expect(access?.eligible_for_narration_stage_review).toBe(true);
    expect(access?.tts_capability).toBe(false);
    expect(access?.renderer_execution_authority).toBe(false);
    const serialized = JSON.stringify(access);
    for (const forbidden of ["output_path", "command", "credential", "job_id", "token"]) {
      expect(serialized).not.toContain(forbidden);
    }
  }, 15_000);

  it.each(["full_render_enabled", "audio_measurement_capability", "final_media_capability"])(
    "rejects forged %s",
    async (field) => {
      const payload = backendPayload();
      payload.access[field] = true;
      const repository = new BackendProductionRepository({
        async request() {
          return { status: 200, payload: payload.access };
        },
      });
      await expect(repository.getExecutionEnablementAccess(payload.run_id)).rejects.toThrow();
    },
    15_000,
  );
});
