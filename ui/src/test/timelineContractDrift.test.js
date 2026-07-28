// @vitest-environment node

import { execFileSync } from "node:child_process";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { BackendProductionRepository } from "../api/productionClient";

const pythonProgram = String.raw`
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.test_plan_only_timeline_planning import (
    _initialize_timeline,
    _timeline_ready_application,
)

with TemporaryDirectory() as directory:
    application, _, run_id, _, _, _, compositions = _timeline_ready_application(
        Path(directory)
    )
    initialized = _initialize_timeline(application, run_id, compositions)
    access = initialized["access"]
    segment = access["collection"]["segments"][0]
    print(json.dumps({
        "run_id": run_id,
        "segment_id": segment["segment_id"],
        "access": access,
        "history": application.get_timeline_segment_history(
            run_id, segment["segment_id"]
        ),
        "segment": segment,
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

describe("timeline Python-to-TypeScript V1 contract drift", () => {
  it("validates integer timing, source authority, history, and false capabilities", async () => {
    const payload = backendPayload();
    const repository = new BackendProductionRepository({
      async request(requestPath) {
        if (requestPath.endsWith("/timeline/access")) {
          return { status: 200, payload: payload.access };
        }
        if (
          requestPath.endsWith(
            `/timeline/segments/${payload.segment_id}/history`,
          )
        ) {
          return { status: 200, payload: payload.history };
        }
        return { status: 404, payload: null };
      },
    });
    const access = await repository.getTimelineAccess(payload.run_id);
    const history = await repository.getTimelineSegmentHistory(
      payload.run_id,
      payload.segment_id,
    );
    expect(access?.collection?.segments[0]).toEqual(payload.segment);
    expect(history?.revisions).toHaveLength(1);
    expect(Number.isInteger(payload.segment.start_ms)).toBe(true);
    expect(payload.segment.end_ms).toBe(
      payload.segment.start_ms + payload.segment.duration_ms,
    );
    expect(payload.segment.narration_source_sha256).toMatch(/^[0-9a-f]{64}$/);
    expect(payload.segment.candidate_artifact_sha256).toMatch(/^[0-9a-f]{64}$/);
    expect(access?.collection?.source_authority_sha256).toMatch(
      /^[0-9a-f]{64}$/,
    );
    expect(access?.timeline_execution_authority).toBe(false);
    expect(access?.audio_generation_capability).toBe(false);
    expect(access?.renderer_execution_authority).toBe(false);
    expect(access?.final_media_capability).toBe(false);

    const forged = structuredClone(payload.access);
    forged.timeline_execution_authority = true;
    const strict = new BackendProductionRepository({
      async request() {
        return { status: 200, payload: forged };
      },
    });
    await expect(strict.getTimelineAccess(payload.run_id)).rejects.toThrow();
  }, 15_000);
});
