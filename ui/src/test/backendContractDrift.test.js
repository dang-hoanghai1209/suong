// @vitest-environment node

import { execFileSync } from "node:child_process";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { BackendProductionRepository } from "../api/productionClient";

const pythonProgram = String.raw`
import json
from tella.topic_production.plan_only_application import PlanOnlyApplication

application = PlanOnlyApplication()
request = {
    "schema_version": 1,
    "input_mode": "TOPIC",
    "source_content": "Learning to trust uncertainty",
    "language": "en",
    "character_scope": "recurring_female",
    "requested_scene_count": 8,
}
created = application.create_run(request)
run_id = created["run"]["run_id"]
review = application.get_review(run_id)
history = application.list_revisions(run_id)
revision = application.get_revision(run_id, "revision-0001")
accepted = application.accept_story_plan(
    run_id,
    {"schema_version": 1, "current_revision_id": "revision-0001"},
)
print(json.dumps({
    "created": created,
    "run": application.get_run(run_id),
    "dashboard": application.list_runs(),
    "capabilities": application.capabilities(),
    "review": review,
    "history": history,
    "revision": revision,
    "accepted": accepted,
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

describe("Python-to-TypeScript V1 contract drift", () => {
  it("validates the actual backend projection through the TypeScript repository", async () => {
    const payload = backendPayload();
    const runId = payload.created.run.run_id;
    const repository = new BackendProductionRepository({
      async request(requestPath, init) {
        if (requestPath.endsWith("/capabilities")) {
          return { status: 200, payload: payload.capabilities };
        }
        if (requestPath.endsWith("/runs") && init?.method === "POST") {
          return { status: 200, payload: payload.created };
        }
        if (requestPath.endsWith("/runs")) {
          return { status: 200, payload: payload.dashboard };
        }
        if (requestPath.endsWith(`/runs/${runId}`)) {
          return { status: 200, payload: payload.run };
        }
        if (requestPath.endsWith(`/runs/${runId}/review/accept`)) {
          return { status: 200, payload: payload.accepted };
        }
        if (requestPath.endsWith(`/runs/${runId}/review`)) {
          return { status: 200, payload: payload.review };
        }
        if (requestPath.endsWith(`/runs/${runId}/revisions/revision-0001`)) {
          return { status: 200, payload: payload.revision };
        }
        if (requestPath.endsWith(`/runs/${runId}/revisions`)) {
          return { status: 200, payload: payload.history };
        }
        return { status: 404, payload: null };
      },
    });
    const request = {
      schema_version: 1,
      input_mode: "TOPIC",
      source_content: "Learning to trust uncertainty",
      language: "en",
      character_scope: "recurring_female",
      requested_scene_count: 8,
    };

    const created = await repository.createPlanOnlyRun(request);
    const run = await repository.getRun(runId);
    const dashboard = await repository.getDashboard();
    const capabilities = await repository.getCapabilities();
    const review = await repository.getStoryPlanReview(runId);
    const history = await repository.getStoryPlanRevisions(runId);
    const revision = await repository.getStoryPlanRevision(
      runId,
      "revision-0001",
    );
    const accepted = await repository.acceptStoryPlan(runId, {
      schema_version: 1,
      current_revision_id: "revision-0001",
    });

    expect(created.run).toEqual(run);
    expect(run.run_id).toBe(runId);
    expect(run.story_plan.semantic_beats.map((beat) => beat.order)).toEqual([
      1, 2, 3, 4, 5, 6, 7, 8,
    ]);
    expect(run.story_plan.scenes.map((scene) => scene.order)).toEqual([
      1, 2, 3, 4, 5, 6, 7, 8,
    ]);
    expect(run.timeline.rows.map((row) => row.order)).toEqual([
      1, 2, 3, 4, 5, 6, 7, 8,
    ]);
    expect(dashboard.runs.map((item) => item.run_id)).toEqual([runId]);
    expect(capabilities.full_render_enabled).toBe(false);
    expect(
      capabilities.render_lock_reason_codes.every((code) => typeof code === "string"),
    ).toBe(true);
    expect(review.review_status).toBe("UNREVIEWED");
    expect(review.render_authority).toBe(false);
    expect(review.media_capability).toBe(false);
    expect(review.current_revision.story_plan.semantic_beats.map((beat) => beat.order)).toEqual([
      1, 2, 3, 4, 5, 6, 7, 8,
    ]);
    expect(history.current_revision_id).toBe("revision-0001");
    expect(revision.revision_id).toBe("revision-0001");
    expect(accepted.review.review_status).toBe("ACCEPTED_FOR_SCENE_PLANNING");
    expect(accepted.review.render_authority).toBe(false);
  });
});
