// @vitest-environment node

import { execFileSync } from "node:child_process";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { BackendProductionRepository } from "../api/productionClient";

const pythonProgram = String.raw`
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from tests.test_plan_only_narration_stage import _sealed_application

with TemporaryDirectory() as directory:
    application, _, _, run_id, package, _ = _sealed_application(Path(directory))
    print(json.dumps({
        "run_id": run_id,
        "package": package,
        "access": application.get_narration_stage_access(run_id),
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

describe("narration-stage Python-to-TypeScript V1 contract drift", () => {
  it("validates package binding, source, provider and false renderer locks", async () => {
    const payload = backendPayload();
    const repository = new BackendProductionRepository({
      async request() {
        return { status: 200, payload: payload.access };
      },
    });
    const access = await repository.getNarrationStageAccess(payload.run_id);
    expect(access?.execution_package_revision_id).toBe(payload.package.package_revision_id);
    expect(access?.narration_source?.narration_source_sha256).toBe(
      payload.package.authority.narration_source_sha256,
    );
    expect(access?.provider_configuration.voice_id).toBe("Callirrhoe");
    expect(access?.provider_configuration.language).toBe("vi-VN");
    expect(access?.renderer_execution_authority).toBe(false);
    expect(access?.final_media_capability).toBe(false);
    const serialized = JSON.stringify(access);
    for (const forbidden of ["api_key", "credential", "artifact_path", "command"]) {
      expect(serialized).not.toContain(forbidden);
    }
  }, 15_000);

  it.each(["renderer_execution_authority", "final_media_capability"])(
    "rejects forged %s",
    async (field) => {
      const payload = backendPayload();
      payload.access[field] = true;
      const repository = new BackendProductionRepository({
        async request() {
          return { status: 200, payload: payload.access };
        },
      });
      await expect(repository.getNarrationStageAccess(payload.run_id)).rejects.toThrow();
    },
    15_000,
  );

  it("rejects credential-shaped response drift", async () => {
    const payload = backendPayload();
    payload.access.provider_configuration.api_key = "secret";
    const repository = new BackendProductionRepository({
      async request() {
        return { status: 200, payload: payload.access };
      },
    });
    await expect(repository.getNarrationStageAccess(payload.run_id)).rejects.toThrow();
  }, 15_000);
});
