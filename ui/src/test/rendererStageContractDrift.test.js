// @vitest-environment node

import { execFileSync } from "node:child_process";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { BackendProductionRepository } from "../api/productionClient";

const pythonProgram = String.raw`
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from tests.test_plan_only_renderer_stage import _renderer_ready_application

with TemporaryDirectory() as directory:
    application, _, _, _, run_id, package, _, accepted = (
        _renderer_ready_application(Path(directory))
    )
    print(json.dumps({
        "run_id": run_id,
        "package": package,
        "accepted": accepted,
        "access": application.get_renderer_stage_access(run_id),
    }, ensure_ascii=True, sort_keys=True))
    application.close()
`;

function backendPayload() {
  const repositoryRoot = path.resolve(process.cwd(), "..");
  const output = execFileSync(
    "uv",
    ["run", "--with", "pytest", "--with", "pytest-asyncio", "python", "-c", pythonProgram],
    {
      cwd: repositoryRoot,
      encoding: "utf8",
      env: { ...process.env, PYTHONPATH: repositoryRoot },
    },
  );
  return JSON.parse(output);
}

function repositoryFor(payload) {
  return new BackendProductionRepository({
    async request() {
      return { status: 200, payload: payload.access };
    },
  });
}

describe("renderer-stage Python-to-TypeScript V1 contract drift", () => {
  it("validates complete upstream binding, configuration, profile and locks", async () => {
    const payload = backendPayload();
    const access = await repositoryFor(payload).getRendererStageAccess(
      payload.run_id,
    );
    expect(access?.input_authority?.execution_package_id).toBe(
      payload.package.package_id,
    );
    expect(access?.input_authority?.narration_artifact_id).toBe(
      payload.accepted.artifact_id,
    );
    expect(access?.renderer_configuration.renderer_implementation_id).toBe(
      "tella.render.pipeline.render",
    );
    expect(access?.output_profile.width).toBe(1080);
    expect(access?.output_profile.height).toBe(1920);
    expect(access?.final_media_capability).toBe(false);
    expect(access?.release_authority).toBe(false);
    expect(access?.publication_authority).toBe(false);
  }, 20_000);

  it.each([
    "full_render_enabled",
    "final_media_capability",
    "release_authority",
    "publication_authority",
  ])("rejects forged %s", async (field) => {
    const payload = backendPayload();
    payload.access[field] = true;
    await expect(
      repositoryFor(payload).getRendererStageAccess(payload.run_id),
    ).rejects.toThrow();
  }, 20_000);

  it.each(["path", "command", "filter_graph", "provider_url", "api_key"])(
    "rejects forbidden response field %s",
    async (field) => {
      const payload = backendPayload();
      payload.access.renderer_configuration[field] = "forbidden";
      await expect(
        repositoryFor(payload).getRendererStageAccess(payload.run_id),
      ).rejects.toThrow();
    },
    20_000,
  );

  it("returns a detached projection", async () => {
    const payload = backendPayload();
    const access = await repositoryFor(payload).getRendererStageAccess(
      payload.run_id,
    );
    payload.access.input_authority.scene_ids[0] = "scene_99";
    expect(access?.input_authority?.scene_ids[0]).toBe("scene_01");
  }, 20_000);
});
