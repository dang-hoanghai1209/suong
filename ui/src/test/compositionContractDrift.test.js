// @vitest-environment node

import { execFileSync } from "node:child_process";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { BackendProductionRepository } from "../api/productionClient";

const pythonProgram = String.raw`
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.test_plan_only_visual_candidates import (
    DeterministicVisualProvider,
    _accepted_scene_application,
    _generate_payload,
    _mutation_payload,
)

with TemporaryDirectory() as directory:
    provider = DeterministicVisualProvider()
    application, run_id, _, scene = _accepted_scene_application(
        Path(directory), provider
    )
    access = application.get_visual_candidate_access(run_id, scene["scene_id"])
    generated = application.generate_visual_candidates(
        run_id, scene["scene_id"], _generate_payload(access, count=1)
    )
    visual = generated["access"]["collection"]
    candidate = visual["candidates"][0]
    accepted = application.mutate_visual_candidate(
        run_id,
        scene["scene_id"],
        candidate["candidate_id"],
        "accept",
        _mutation_payload(visual, candidate),
    )
    visual = accepted["access"]["collection"]
    initialized = application.initialize_compositions(
        run_id,
        {
            "schema_version": 1,
            "scene_plan_collection_revision_id": visual[
                "scene_plan_collection_revision_id"
            ],
        },
    )
    composition_access = initialized["access"]
    composition = composition_access["collection"]["compositions"][0]
    print(json.dumps({
        "run_id": run_id,
        "scene_id": scene["scene_id"],
        "access": composition_access,
        "history": application.get_composition_history(run_id, scene["scene_id"]),
        "composition": composition,
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

describe("composition Python-to-TypeScript V1 contract drift", () => {
  it("validates source bindings, geometry, history, and false capabilities", async () => {
    const payload = backendPayload();
    const repository = new BackendProductionRepository({
      async request(requestPath) {
        if (requestPath.endsWith("/compositions/access")) {
          return { status: 200, payload: payload.access };
        }
        if (requestPath.endsWith(`/compositions/scenes/${payload.scene_id}/history`)) {
          return { status: 200, payload: payload.history };
        }
        return { status: 404, payload: null };
      },
    });
    const access = await repository.getCompositionAccess(payload.run_id);
    const history = await repository.getCompositionHistory(
      payload.run_id,
      payload.scene_id,
    );
    expect(access.collection.compositions[0]).toEqual(payload.composition);
    expect(history.revisions).toHaveLength(1);
    expect(payload.composition.accepted_candidate_sha256).toMatch(
      /^[0-9a-f]{64}$/,
    );
    expect(payload.composition.aspect_ratio).toBe("9:16");
    expect(payload.composition.layers[0].layer_type).toBe("ACCEPTED_VISUAL");
    expect(access.render_authority).toBe(false);
    expect(access.timeline_execution_authority).toBe(false);
    expect(access.final_media_capability).toBe(false);
    expect(JSON.stringify(access)).not.toContain("\\\\");

    const forged = structuredClone(payload.access);
    forged.renderer_execution_authority = true;
    const strict = new BackendProductionRepository({
      async request() {
        return { status: 200, payload: forged };
      },
    });
    await expect(strict.getCompositionAccess(payload.run_id)).rejects.toThrow();
  }, 15_000);
});
