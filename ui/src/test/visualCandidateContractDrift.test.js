// @vitest-environment node

import { execFileSync } from "node:child_process";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { BackendProductionRepository } from "../api/productionClient";

const pythonProgram = String.raw`
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from PIL import Image

from tella.media.image_provider_contract import ImageProviderCapabilities
from tella.topic_production.plan_only_application import PlanOnlyApplication

class Provider:
    provider_name = "contract-test-provider"
    def capabilities(self):
        return ImageProviderCapabilities(
            provider_id=self.provider_name,
            supports_text_to_image=True,
            supports_reference_conditioning=False,
            supports_image_to_image=False,
            supports_structural_conditioning=False,
            supports_seed=True,
            supports_negative_prompt=True,
            max_prompt_utf8_bytes=12000,
            max_reference_images=0,
            accepted_reference_mime_types=(),
            supports_character_identity_anchor=False,
            provider_retry_control="caller_bounded",
        )
    def is_configured(self):
        return True
    async def generate_text_image(
        self, prompt, negative_prompt, aspect, seed, out_path, metadata=None
    ):
        color = (seed % 251, (seed * 2) % 251, (seed * 3) % 251)
        Image.new("RGB", (576, 1024), color).save(out_path, format="PNG")
        return SimpleNamespace(output_path=out_path)

with TemporaryDirectory() as directory:
    application = PlanOnlyApplication(
        visual_provider=Provider(),
        visual_artifact_root=Path(directory),
    )
    created = application.create_run({
        "schema_version": 1,
        "input_mode": "TOPIC",
        "source_content": "A calm story about choosing a nourishing daily ritual",
        "language": "en",
        "character_scope": "recurring_female",
        "requested_scene_count": 8,
    })
    run_id = created["run"]["run_id"]
    application.accept_story_plan(
        run_id, {"schema_version": 1, "current_revision_id": "revision-0001"}
    )
    initialized = application.initialize_scene_plan(
        run_id, {"schema_version": 1, "accepted_story_revision_id": "revision-0001"}
    )
    scene_collection = initialized["access"]["collection"]
    scene = scene_collection["scenes"][0]
    accepted_scene = application.mutate_scene_plan(
        run_id,
        "accept",
        {
            "schema_version": 1,
            "base_collection_revision_id": scene_collection["collection_revision_id"],
            "scene_id": scene["scene_id"],
            "base_scene_revision_id": scene["scene_revision_id"],
        },
        scene_id=scene["scene_id"],
    )
    scene = accepted_scene["access"]["collection"]["scenes"][0]
    access = application.get_visual_candidate_access(run_id, scene["scene_id"])
    collection = access["collection"]
    generated = application.generate_visual_candidates(
        run_id,
        scene["scene_id"],
        {
            "schema_version": 1,
            "visual_collection_revision_id": collection["visual_collection_revision_id"],
            "scene_plan_collection_revision_id": collection[
                "scene_plan_collection_revision_id"
            ],
            "scene_revision_id": collection["scene_revision_id"],
            "candidate_count": 2,
            "aspect_ratio": "9:16",
            "composition_emphasis": None,
            "correction_dimensions": [],
            "note": None,
        },
    )
    collection = generated["access"]["collection"]
    first, second = collection["candidates"]
    def mutation(candidate, reason):
        return {
            "schema_version": 1,
            "visual_collection_revision_id": collection[
                "visual_collection_revision_id"
            ],
            "scene_plan_collection_revision_id": collection[
                "scene_plan_collection_revision_id"
            ],
            "scene_revision_id": collection["scene_revision_id"],
            "candidate_revision_id": candidate["candidate_revision_id"],
            "reason_code": reason,
            "note": "Bounded contract review.",
        }
    rejected = application.mutate_visual_candidate(
        run_id, scene["scene_id"], first["candidate_id"], "reject",
        mutation(first, "COMPOSITION_MISMATCH"),
    )
    collection = rejected["access"]["collection"]
    second = collection["candidates"][1]
    accepted = application.mutate_visual_candidate(
        run_id, scene["scene_id"], second["candidate_id"], "accept",
        mutation(second, "OTHER_BOUNDED_NOTE"),
    )
    collection = accepted["access"]["collection"]
    regenerated = application.generate_visual_candidates(
        run_id,
        scene["scene_id"],
        {
            "schema_version": 1,
            "visual_collection_revision_id": collection[
                "visual_collection_revision_id"
            ],
            "scene_plan_collection_revision_id": collection[
                "scene_plan_collection_revision_id"
            ],
            "scene_revision_id": collection["scene_revision_id"],
            "candidate_count": 1,
            "aspect_ratio": "9:16",
            "composition_emphasis": None,
            "correction_dimensions": [],
            "note": None,
        },
    )
    collection = regenerated["access"]["collection"]
    newest = collection["candidates"][-1]
    final = application.mutate_visual_candidate(
        run_id, scene["scene_id"], newest["candidate_id"], "accept",
        mutation(newest, "OTHER_BOUNDED_NOTE"),
    )
    final_access = final["access"]
    candidate = final_access["collection"]["candidates"][-1]
    superseded = final_access["collection"]["candidates"][1]
    print(json.dumps({
        "run_id": run_id,
        "scene_id": scene["scene_id"],
        "initial_access": access,
        "generated": {"schema_version": 1, "access": final_access, "error": None},
        "candidate": application.get_visual_candidate(
            run_id, scene["scene_id"], candidate["candidate_id"]
        ),
        "superseded": superseded,
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

describe("visual candidate Python-to-TypeScript V1 contract drift", () => {
  it("validates actual access, collection, histories, candidates, and authority locks", async () => {
    const payload = backendPayload();
    const generatedAccess = payload.generated.access;
    const repository = new BackendProductionRepository({
      async request(requestPath) {
        if (requestPath.endsWith("/visual-candidates")) {
          return { status: 200, payload: generatedAccess };
        }
        if (requestPath.endsWith(`/visual-candidates/${payload.candidate.candidate_id}`)) {
          return { status: 200, payload: payload.candidate };
        }
        return { status: 404, payload: null };
      },
    });
    const access = await repository.getVisualCandidateAccess(
      payload.run_id,
      payload.scene_id,
    );
    const candidate = await repository.getVisualCandidate(
      payload.run_id,
      payload.scene_id,
      payload.candidate.candidate_id,
    );
    expect(access.collection.requests).toHaveLength(2);
    expect(access.collection.attempts).toHaveLength(2);
    expect(access.collection.candidates).toHaveLength(3);
    expect(access.collection.current_accepted_candidate_id).toBe(
      payload.candidate.candidate_id,
    );
    expect(candidate.accepted_for_composition_planning).toBe(true);
    expect(candidate.review_history).toHaveLength(1);
    expect(access.collection.candidates[1].status).toBe("SUPERSEDED");
    expect(access.collection.candidates[1].review_history).toHaveLength(2);
    expect(candidate.sha256).toMatch(/^[0-9a-f]{64}$/);
    expect(candidate.mime_type).toBe("image/png");
    expect([candidate.width, candidate.height]).toEqual([576, 1024]);
    expect(access.render_authority).toBe(false);
    expect(access.final_media_capability).toBe(false);
    expect(JSON.stringify(access)).not.toContain("\\\\");
    expect(JSON.stringify(access)).not.toMatch(/api[_-]?key|authorization/i);

    const forged = structuredClone(generatedAccess);
    forged.render_authority = true;
    const strictRepository = new BackendProductionRepository({
      async request() {
        return { status: 200, payload: forged };
      },
    });
    await expect(
      strictRepository.getVisualCandidateAccess(payload.run_id, payload.scene_id),
    ).rejects.toThrow("invalid production response");
  }, 15_000);
});
