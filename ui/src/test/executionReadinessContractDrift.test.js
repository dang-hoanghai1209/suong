// @vitest-environment node

import { execFileSync } from "node:child_process";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { BackendProductionRepository } from "../api/productionClient";

const pythonProgram = String.raw`
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.test_plan_only_execution_readiness import (
    _acknowledge_all,
    _initialize_readiness,
    _readiness_ready_application,
)

with TemporaryDirectory() as directory:
    application, _, run_id, _, _, _, timeline = _readiness_ready_application(
        Path(directory)
    )
    initial = _initialize_readiness(application, run_id, timeline)["report"]
    report = _acknowledge_all(application, run_id, initial)["report"]
    print(json.dumps({
        "run_id": run_id,
        "access": application.get_execution_readiness_access(run_id),
        "report": report,
        "history": application.get_execution_readiness_history(run_id),
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

describe("execution-readiness Python-to-TypeScript V1 contract drift", () => {
  it("validates authority, scenes, limitations, history, and false capabilities", async () => {
    const payload = backendPayload();
    const repository = new BackendProductionRepository({
      async request(requestPath) {
        if (requestPath.endsWith("/execution-readiness/access")) {
          return { status: 200, payload: payload.access };
        }
        if (requestPath.endsWith("/execution-readiness/history")) {
          return { status: 200, payload: payload.history };
        }
        return { status: 404, payload: null };
      },
    });
    const access = await repository.getExecutionReadinessAccess(payload.run_id);
    const history = await repository.getExecutionReadinessHistory(payload.run_id);
    expect(access?.report).toEqual(payload.report);
    expect(history?.reports).toHaveLength(2);
    expect(access?.report?.scene_checks).toHaveLength(8);
    expect(access?.report?.limitations).toHaveLength(15);
    expect(access?.report?.acknowledgements).toHaveLength(15);
    expect(access?.report?.source_authority_sha256).toMatch(/^[0-9a-f]{64}$/);
    expect(access?.execution_job_creation_capability).toBe(false);
    expect(access?.renderer_execution_authority).toBe(false);
    expect(JSON.stringify(access)).not.toContain("artifact_url");
    expect(JSON.stringify(access)).not.toContain("execution_token");

    const forged = structuredClone(payload.access);
    forged.media_muxing_capability = true;
    const strict = new BackendProductionRepository({
      async request() {
        return { status: 200, payload: forged };
      },
    });
    await expect(
      strict.getExecutionReadinessAccess(payload.run_id),
    ).rejects.toThrow();
  }, 15_000);
});
