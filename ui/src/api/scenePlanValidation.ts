import type {
  PublicApiErrorV1,
  ScenePlanAccessV1,
  ScenePlanCollectionV1,
  ScenePlanCollectionHistoryV1,
  ScenePlanOperationResultV1,
  ScenePlanSceneHistoryV1,
  ScenePlanStatusV1,
  ScenePlanV1,
} from "../contracts/v1/production";
import { ProductionContractError } from "./productionClient";

const statuses = new Set<ScenePlanStatusV1>([
  "DRAFT",
  "VALID",
  "WARNING",
  "BLOCKED",
  "ACCEPTED_FOR_VISUAL_PLANNING",
  "REVISION_REQUESTED",
]);

function record(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new ProductionContractError();
  }
  return value as Record<string, unknown>;
}

function stringValue(value: unknown): string {
  if (typeof value !== "string") throw new ProductionContractError();
  return value;
}

function booleanValue(value: unknown): boolean {
  if (typeof value !== "boolean") throw new ProductionContractError();
  return value;
}

function numberValue(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new ProductionContractError();
  }
  return value;
}

function positiveInteger(value: unknown): number {
  const number = numberValue(value);
  if (!Number.isInteger(number) || number < 1) throw new ProductionContractError();
  return number;
}

function schemaOne(value: Record<string, unknown>) {
  if (value.schema_version !== 1) throw new ProductionContractError();
}

function strings(value: unknown): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    throw new ProductionContractError();
  }
  return Array.from(value);
}

function validateError(value: unknown): PublicApiErrorV1 {
  const error = record(value);
  schemaOne(error);
  if (
    !["BLOCKED", "FAILED"].includes(stringValue(error.status)) ||
    typeof error.retryable !== "boolean" ||
    !Array.isArray(error.field_errors)
  ) {
    throw new ProductionContractError();
  }
  return {
    schema_version: 1,
    request_id: stringValue(error.request_id),
    status: error.status as PublicApiErrorV1["status"],
    code: stringValue(error.code),
    message: stringValue(error.message),
    retryable: error.retryable,
    field_errors: error.field_errors.map((raw) => {
      const field = record(raw);
      return {
        path: stringValue(field.path),
        code: stringValue(field.code),
        message: stringValue(field.message),
      };
    }),
    details: structuredClone(record(error.details)),
  };
}

export function validateScenePlan(value: unknown): ScenePlanV1 {
  const scene = record(value);
  schemaOne(scene);
  const coverage = record(scene.source_coverage);
  const continuity = record(scene.continuity);
  const status = stringValue(scene.status);
  const start = numberValue(coverage.start);
  const end = numberValue(coverage.end);
  const duration = numberValue(scene.planned_duration_seconds);
  const warningCodes = strings(scene.warning_codes);
  const blockerCodes = strings(scene.blocker_codes);
  if (
    !statuses.has(status as ScenePlanStatusV1) ||
    start < 0 ||
    !Number.isInteger(start) ||
    !Number.isInteger(end) ||
    end <= start ||
    Array.from(stringValue(scene.narration_segment)).length !== end - start ||
    duration <= 0 ||
    new Set(warningCodes).size !== warningCodes.length ||
    new Set(blockerCodes).size !== blockerCodes.length ||
    continuity.identity_continuity_required !== true ||
    continuity.environment_continuity !== "PLANNING_REQUIRED" ||
    continuity.object_continuity !== "PLANNING_REQUIRED" ||
    continuity.visual_identity_verified !== false
  ) {
    throw new ProductionContractError();
  }
  return {
    schema_version: 1,
    run_id: stringValue(scene.run_id),
    source_story_revision_id: stringValue(scene.source_story_revision_id),
    scene_id: stringValue(scene.scene_id),
    scene_revision_id: stringValue(scene.scene_revision_id),
    scene_revision_number: positiveInteger(scene.scene_revision_number),
    order: positiveInteger(scene.order),
    source_beat_id: stringValue(scene.source_beat_id),
    source_coverage: {
      start,
      end,
      overlap_draft: booleanValue(coverage.overlap_draft),
    },
    narration_segment: stringValue(scene.narration_segment),
    objective: stringValue(scene.objective),
    emotional_intent: stringValue(scene.emotional_intent),
    environment: stringValue(scene.environment),
    character_action: stringValue(scene.character_action),
    objects: strings(scene.objects),
    composition_guidance: stringValue(scene.composition_guidance),
    continuity_notes: stringValue(scene.continuity_notes),
    camera_motion_intent: stringValue(scene.camera_motion_intent),
    transition_intent: stringValue(scene.transition_intent),
    planned_duration_seconds: duration,
    planning_note: stringValue(scene.planning_note),
    status: status as ScenePlanStatusV1,
    warning_codes: warningCodes,
    blocker_codes: blockerCodes,
    continuity: {
      recurring_character_required: booleanValue(
        continuity.recurring_character_required,
      ),
      anonymous_background_allowed: booleanValue(
        continuity.anonymous_background_allowed,
      ),
      identity_continuity_required: true,
      environment_continuity: "PLANNING_REQUIRED",
      object_continuity: "PLANNING_REQUIRED",
      visual_identity_verified: false,
    },
    created_at: stringValue(scene.created_at),
  };
}

export function validateScenePlanCollection(value: unknown): ScenePlanCollectionV1 {
  const collection = record(value);
  schemaOne(collection);
  if (!Array.isArray(collection.scenes) || !Array.isArray(collection.revision_history)) {
    throw new ProductionContractError();
  }
  const scenes = collection.scenes.map(validateScenePlan);
  const expected = scenes.map((_, index) => index + 1);
  const sceneIds = scenes.map((scene) => scene.scene_id);
  if (
    scenes.some((scene, index) => scene.order !== expected[index]) ||
    new Set(sceneIds).size !== sceneIds.length ||
    scenes.some(
      (scene) =>
        scene.run_id !== collection.run_id ||
        scene.source_story_revision_id !== collection.source_story_revision_id,
    )
  ) {
    throw new ProductionContractError();
  }
  const histories = collection.revision_history.map((raw) => {
    const summary = record(raw);
    return {
      collection_revision_id: stringValue(summary.collection_revision_id),
      revision_number: positiveInteger(summary.revision_number),
      created_at: stringValue(summary.created_at),
      reason: stringValue(summary.reason),
      affected_scene_ids: strings(summary.affected_scene_ids),
    };
  });
  const number = positiveInteger(collection.collection_revision_number);
  const historyIds = histories.map((item) => item.collection_revision_id);
  if (
    histories.some((item, index) => item.revision_number !== index + 1) ||
    histories.length !== number ||
    new Set(historyIds).size !== historyIds.length ||
    historyIds.at(-1) !== collection.collection_revision_id ||
    collection.collection_revision_id !==
      `scene-plan-revision-${number.toString().padStart(4, "0")}` ||
    collection.source_story_revision_id !==
      collection.accepted_story_revision_id ||
    histories.at(-1)?.created_at !== collection.created_at ||
    histories.at(-1)?.reason !== collection.reason
  ) {
    throw new ProductionContractError();
  }
  const total = numberValue(collection.total_planned_duration_seconds);
  const computed = scenes.reduce(
    (sum, scene) => sum + scene.planned_duration_seconds,
    0,
  );
  const sourceLength = positiveInteger(collection.source_narration_length);
  const canonicalCoverage = scenes
    .filter((scene) => !scene.source_coverage.overlap_draft)
    .map((scene) => scene.source_coverage)
    .sort((left, right) => left.start - right.start || left.end - right.end);
  const coverageValid =
    canonicalCoverage.length > 0 &&
    canonicalCoverage[0]?.start === 0 &&
    canonicalCoverage.at(-1)?.end === sourceLength &&
    canonicalCoverage.every(
      (coverage, index) =>
        index === 0 || coverage.start === canonicalCoverage[index - 1]?.end,
    );
  const durationValid = total >= 32 && total <= 38;
  const ready =
    durationValid &&
    coverageValid &&
    scenes.every(
      (scene) =>
        scene.blocker_codes.length === 0 &&
        scene.warning_codes.length === 0 &&
        !scene.source_coverage.overlap_draft,
    );
  if (
    Math.abs(total - computed) > 1e-9 ||
    collection.duration_valid !== durationValid ||
    collection.source_coverage_valid !== coverageValid ||
    collection.ready_for_visual_planning !== ready ||
    collection.render_authority !== false ||
    collection.media_capability !== false ||
    collection.process_local !== true
  ) {
    throw new ProductionContractError();
  }
  return {
    schema_version: 1,
    run_id: stringValue(collection.run_id),
    source_story_revision_id: stringValue(collection.source_story_revision_id),
    accepted_story_revision_id: stringValue(collection.accepted_story_revision_id),
    collection_revision_id: stringValue(collection.collection_revision_id),
    collection_revision_number: number,
    created_at: stringValue(collection.created_at),
    reason: stringValue(collection.reason),
    source_narration_length: sourceLength,
    scenes,
    revision_history: histories,
    target_duration_seconds: numberValue(collection.target_duration_seconds),
    target_min_seconds:
      collection.target_min_seconds === 32
        ? 32
        : (() => {
            throw new ProductionContractError();
          })(),
    target_max_seconds:
      collection.target_max_seconds === 38
        ? 38
        : (() => {
            throw new ProductionContractError();
          })(),
    total_planned_duration_seconds: total,
    duration_valid: booleanValue(collection.duration_valid),
    source_coverage_valid: booleanValue(collection.source_coverage_valid),
    ready_for_visual_planning: booleanValue(collection.ready_for_visual_planning),
    render_authority: false,
    media_capability: false,
    process_local: true,
  };
}

export function validateScenePlanAccess(value: unknown): ScenePlanAccessV1 {
  const access = record(value);
  schemaOne(access);
  if (
    access.render_authority !== false ||
    access.media_capability !== false ||
    access.process_local !== true
  ) {
    throw new ProductionContractError();
  }
  const runId = stringValue(access.run_id);
  const collection =
    access.collection === null
      ? null
      : validateScenePlanCollection(access.collection);
  if (
    collection !== null &&
    (collection.run_id !== runId ||
      collection.source_story_revision_id !==
        collection.accepted_story_revision_id)
  ) {
    throw new ProductionContractError();
  }
  const blockers = strings(access.blocker_codes);
  const editable = booleanValue(access.editable);
  if (editable === (blockers.length > 0)) {
    throw new ProductionContractError();
  }
  return {
    schema_version: 1,
    run_id: runId,
    editable,
    blocker_codes: blockers,
    collection,
    render_authority: false,
    media_capability: false,
    process_local: true,
  };
}

export function validateScenePlanOperation(
  value: unknown,
): ScenePlanOperationResultV1 {
  const result = record(value);
  schemaOne(result);
  const access =
    result.access === null ? null : validateScenePlanAccess(result.access);
  const error = result.error === null ? null : validateError(result.error);
  if ((access === null) === (error === null)) throw new ProductionContractError();
  return { schema_version: 1, access, error };
}

export function validateSceneHistory(value: unknown): ScenePlanSceneHistoryV1 {
  const history = record(value);
  schemaOne(history);
  if (!Array.isArray(history.revisions)) throw new ProductionContractError();
  const revisions = history.revisions.map(validateScenePlan);
  const ids = revisions.map((item) => item.scene_revision_id);
  if (
    !revisions.length ||
    new Set(ids).size !== ids.length ||
    ids.at(-1) !== history.current_scene_revision_id
  ) {
    throw new ProductionContractError();
  }
  return {
    schema_version: 1,
    run_id: stringValue(history.run_id),
    scene_id: stringValue(history.scene_id),
    current_scene_revision_id: stringValue(history.current_scene_revision_id),
    revisions,
  };
}

export function validateScenePlanCollectionHistory(
  value: unknown,
): ScenePlanCollectionHistoryV1 {
  const history = record(value);
  schemaOne(history);
  if (!Array.isArray(history.revisions)) throw new ProductionContractError();
  const revisions = history.revisions.map((raw) => {
    const revision = record(raw);
    return {
      collection_revision_id: stringValue(revision.collection_revision_id),
      revision_number: positiveInteger(revision.revision_number),
      created_at: stringValue(revision.created_at),
      reason: stringValue(revision.reason),
      affected_scene_ids: strings(revision.affected_scene_ids),
    };
  });
  const ids = revisions.map((item) => item.collection_revision_id);
  if (
    !revisions.length ||
    revisions.some((item, index) => item.revision_number !== index + 1) ||
    new Set(ids).size !== ids.length ||
    ids.at(-1) !== history.current_collection_revision_id
  ) {
    throw new ProductionContractError();
  }
  return {
    schema_version: 1,
    run_id: stringValue(history.run_id),
    current_collection_revision_id: stringValue(
      history.current_collection_revision_id,
    ),
    revisions,
  };
}
