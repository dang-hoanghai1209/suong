import type {
  CompositionAccessV1,
  CompositionCollectionV1,
  CompositionFitModeV1,
  CompositionMotionModeV1,
  CompositionOperationResultV1,
  CompositionStatusV1,
  CompositionTransitionV1,
  NormalizedCropV1,
  NormalizedGeometryV1,
  SceneCompositionV1,
} from "../contracts/v1/production";
import { ProductionContractError } from "./productionClient";

const sha = /^[0-9a-f]{64}$/;
const statuses = new Set<CompositionStatusV1>([
  "DRAFT",
  "VALID",
  "WARNING",
  "BLOCKED",
  "ACCEPTED_FOR_TIMELINE_PLANNING",
  "REVISION_REQUESTED",
  "SUPERSEDED",
]);
const fits = new Set<CompositionFitModeV1>([
  "CONTAIN",
  "COVER",
  "FIT_WIDTH",
  "FIT_HEIGHT",
  "MANUAL_CROP",
]);
const motions = new Set<CompositionMotionModeV1>([
  "STATIC",
  "SLOW_ZOOM_IN",
  "SLOW_ZOOM_OUT",
  "PAN_LEFT",
  "PAN_RIGHT",
  "PAN_UP",
  "PAN_DOWN",
  "CUSTOM_START_END",
]);
const transitions = new Set<CompositionTransitionV1>([
  "CUT",
  "CROSSFADE",
  "FADE_THROUGH_COLOR",
  "DIP_TO_BLACK",
  "HOLD_THEN_CUT",
]);

function record(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new ProductionContractError();
  }
  return value as Record<string, unknown>;
}
function text(value: unknown): string {
  if (typeof value !== "string") throw new ProductionContractError();
  return value;
}
function nullableText(value: unknown): string | null {
  return value === null ? null : text(value);
}
function bool(value: unknown): boolean {
  if (typeof value !== "boolean") throw new ProductionContractError();
  return value;
}
function integer(value: unknown, minimum = 1): number {
  if (!Number.isInteger(value) || (value as number) < minimum) {
    throw new ProductionContractError();
  }
  return value as number;
}
function number(value: unknown, minimum: number, maximum: number): number {
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    value < minimum ||
    value > maximum
  ) {
    throw new ProductionContractError();
  }
  return value;
}
function strings(value: unknown): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    throw new ProductionContractError();
  }
  return [...value];
}
function schemaOne(value: Record<string, unknown>): void {
  if (value.schema_version !== 1) throw new ProductionContractError();
}
function falseAuthority(value: Record<string, unknown>, keys: readonly string[]) {
  if (keys.some((key) => value[key] !== false)) {
    throw new ProductionContractError("Execution authority must remain false.");
  }
}
function geometry(value: unknown): NormalizedGeometryV1 {
  const item = record(value);
  const result = {
    x: number(item.x, 0, 1),
    y: number(item.y, 0, 1),
    width: number(item.width, Number.MIN_VALUE, 1),
    height: number(item.height, Number.MIN_VALUE, 1),
    anchor_x: number(item.anchor_x, 0, 1),
    anchor_y: number(item.anchor_y, 0, 1),
    scale: number(item.scale, 0.5, 2),
    rotation_degrees: number(item.rotation_degrees, -10, 10),
    opacity: number(item.opacity, 0, 1),
  };
  if (result.x + result.width > 1 || result.y + result.height > 1) {
    throw new ProductionContractError();
  }
  return result;
}
function crop(value: unknown): NormalizedCropV1 {
  const item = record(value);
  const result = {
    x: number(item.x, 0, 1),
    y: number(item.y, 0, 1),
    width: number(item.width, Number.MIN_VALUE, 1),
    height: number(item.height, Number.MIN_VALUE, 1),
  };
  if (
    result.x + result.width > 1 ||
    result.y + result.height > 1 ||
    result.width * result.height < 0.1
  ) {
    throw new ProductionContractError();
  }
  return result;
}

export function validateSceneComposition(value: unknown): SceneCompositionV1 {
  const item = record(value);
  schemaOne(item);
  falseAuthority(item, [
    "render_authority",
    "renderer_execution_authority",
    "video_render_authority",
    "timeline_execution_authority",
    "final_media_capability",
    "narration_generation_capability",
    "tts_capability",
  ]);
  const status = text(item.status) as CompositionStatusV1;
  const fit = text(item.fit_mode) as CompositionFitModeV1;
  const artifactUrl = text(item.artifact_url);
  if (
    !statuses.has(status) ||
    !fits.has(fit) ||
    !sha.test(text(item.accepted_candidate_sha256)) ||
    !artifactUrl.startsWith("/api/v1/plan-only/") ||
    artifactUrl.includes("://") ||
    text(item.aspect_ratio) !== "9:16"
  ) {
    throw new ProductionContractError();
  }
  const placement = geometry(item.placement);
  const sourceCrop = crop(item.crop);
  const sourceCoverage = record(item.source_coverage);
  const start = integer(sourceCoverage.start, 0);
  const end = integer(sourceCoverage.end);
  if (end <= start) throw new ProductionContractError();
  const motion = record(item.motion_intent);
  const mode = text(motion.mode) as CompositionMotionModeV1;
  if (!motions.has(mode)) throw new ProductionContractError();
  const transition = text(item.transition_intent) as CompositionTransitionV1;
  if (!transitions.has(transition)) throw new ProductionContractError();
  const layersRaw = item.layers;
  if (!Array.isArray(layersRaw) || layersRaw.length === 0) {
    throw new ProductionContractError();
  }
  const layers = layersRaw.map((value, index) => {
    const layer = record(value);
    const layerType = text(layer.layer_type);
    if (
      !["ACCEPTED_VISUAL", "SAFE_COLOR_WASH", "SAFE_GRADIENT_OVERLAY"].includes(
        layerType,
      ) ||
      integer(layer.z_order, 0) !== index ||
      (index === 0 && layerType !== "ACCEPTED_VISUAL")
    ) {
      throw new ProductionContractError();
    }
    return {
      layer_id: text(layer.layer_id),
      layer_type: layerType as
        | "ACCEPTED_VISUAL"
        | "SAFE_COLOR_WASH"
        | "SAFE_GRADIENT_OVERLAY",
      z_order: index,
      enabled: bool(layer.enabled),
      geometry: geometry(layer.geometry),
      opacity: number(layer.opacity, 0, 1),
      color: nullableText(layer.color),
    };
  });
  const validation = record(item.validation);
  const safeMargins = record(item.safe_margins);
  const accepted = bool(item.accepted_for_timeline_planning);
  if (accepted !== (status === "ACCEPTED_FOR_TIMELINE_PLANNING")) {
    throw new ProductionContractError();
  }
  if (
    item.composition_contract_version !== "composition_planning_v1" ||
    !Array.isArray(item.review_history)
  ) {
    throw new ProductionContractError();
  }
  const reviews = item.review_history.map((value) => {
    const review = record(value);
    const action = text(review.action);
    if (!["ACCEPTED", "REVISION_REQUESTED", "SUPERSEDED"].includes(action)) {
      throw new ProductionContractError();
    }
    return {
      review_id: text(review.review_id),
      action: action as "ACCEPTED" | "REVISION_REQUESTED" | "SUPERSEDED",
      reason_code: text(review.reason_code),
      note: nullableText(review.note),
      composition_revision_id: text(review.composition_revision_id),
      created_at: text(review.created_at),
    };
  });
  return {
    schema_version: 1,
    composition_id: text(item.composition_id),
    composition_revision_id: text(item.composition_revision_id),
    composition_revision_number: integer(item.composition_revision_number),
    position: integer(item.position),
    run_id: text(item.run_id),
    story_revision_id: text(item.story_revision_id),
    scene_plan_collection_revision_id: text(item.scene_plan_collection_revision_id),
    scene_id: text(item.scene_id),
    scene_revision_id: text(item.scene_revision_id),
    visual_collection_revision_id: text(item.visual_collection_revision_id),
    accepted_candidate_id: text(item.accepted_candidate_id),
    accepted_candidate_revision_id: text(item.accepted_candidate_revision_id),
    accepted_candidate_sha256: text(item.accepted_candidate_sha256),
    artifact_url: artifactUrl,
    source_width: integer(item.source_width, 64),
    source_height: integer(item.source_height, 64),
    source_mime: text(item.source_mime) as
      | "image/png"
      | "image/jpeg"
      | "image/webp",
    source_coverage: { start, end },
    semantic_beat_id: text(item.semantic_beat_id),
    planned_duration_seconds: number(item.planned_duration_seconds, Number.MIN_VALUE, 60),
    aspect_ratio: "9:16",
    composition_contract_version: "composition_planning_v1",
    status,
    fit_mode: fit,
    crop: sourceCrop,
    placement,
    safe_margins: {
      top: number(safeMargins.top, 0.08, 0.08) as 0.08,
      bottom: number(safeMargins.bottom, 0.12, 0.12) as 0.12,
      left: number(safeMargins.left, 0.06, 0.06) as 0.06,
      right: number(safeMargins.right, 0.06, 0.06) as 0.06,
      title_safe: number(safeMargins.title_safe, 0.1, 0.1) as 0.1,
      subtitle_safe: number(safeMargins.subtitle_safe, 0.16, 0.16) as 0.16,
    },
    layers,
    motion_intent: {
      mode,
      start_scale: number(motion.start_scale, 0.8, 1.5),
      end_scale: number(motion.end_scale, 0.8, 1.5),
      start_anchor_x: number(motion.start_anchor_x, 0, 1),
      start_anchor_y: number(motion.start_anchor_y, 0, 1),
      end_anchor_x: number(motion.end_anchor_x, 0, 1),
      end_anchor_y: number(motion.end_anchor_y, 0, 1),
    },
    transition_intent: transition,
    transition_duration_seconds: number(item.transition_duration_seconds, 0, 2),
    note: nullableText(item.note),
    validation: {
      valid: bool(validation.valid),
      blocker_codes: strings(validation.blocker_codes),
      warning_codes: strings(validation.warning_codes),
      information_codes: strings(validation.information_codes),
    },
    continuity_codes: strings(item.continuity_codes),
    changed_fields: strings(item.changed_fields),
    review_history: reviews,
    accepted_for_timeline_planning: accepted,
    superseded_reason: nullableText(item.superseded_reason),
    created_at: text(item.created_at),
    render_authority: false,
    renderer_execution_authority: false,
    video_render_authority: false,
    timeline_execution_authority: false,
    final_media_capability: false,
    narration_generation_capability: false,
    tts_capability: false,
  };
}

export function validateCompositionCollection(
  value: unknown,
): CompositionCollectionV1 {
  const item = record(value);
  schemaOne(item);
  falseAuthority(item, [
    "render_authority",
    "renderer_execution_authority",
    "video_render_authority",
    "timeline_execution_authority",
    "final_media_capability",
  ]);
  if (!Array.isArray(item.compositions)) throw new ProductionContractError();
  const compositions = item.compositions.map(validateSceneComposition);
  const positions = compositions.map((composition) => composition.position);
  const sceneIds = compositions.map((composition) => composition.scene_id);
  if (
    positions.some((position, index) => position !== index + 1) ||
    new Set(sceneIds).size !== sceneIds.length
  ) {
    throw new ProductionContractError();
  }
  return {
    schema_version: 1,
    collection_id: text(item.collection_id),
    collection_revision_id: text(item.collection_revision_id),
    collection_revision_number: integer(item.collection_revision_number),
    run_id: text(item.run_id),
    story_revision_id: text(item.story_revision_id),
    scene_plan_collection_revision_id: text(item.scene_plan_collection_revision_id),
    compositions,
    missing_scene_ids: strings(item.missing_scene_ids),
    total_scene_count: integer(item.total_scene_count),
    valid_composition_count: integer(item.valid_composition_count, 0),
    warning_composition_count: integer(item.warning_composition_count, 0),
    blocked_composition_count: integer(item.blocked_composition_count, 0),
    accepted_for_timeline_planning_count: integer(
      item.accepted_for_timeline_planning_count,
      0,
    ),
    overall_ready_for_timeline_planning: bool(
      item.overall_ready_for_timeline_planning,
    ),
    created_at: text(item.created_at),
    render_authority: false,
    renderer_execution_authority: false,
    video_render_authority: false,
    timeline_execution_authority: false,
    final_media_capability: false,
  };
}

export function validateCompositionAccess(value: unknown): CompositionAccessV1 {
  const item = record(value);
  schemaOne(item);
  falseAuthority(item, [
    "render_authority",
    "renderer_execution_authority",
    "video_render_authority",
    "timeline_execution_authority",
    "final_media_capability",
    "narration_generation_capability",
    "tts_capability",
  ]);
  if (item.process_local !== true) throw new ProductionContractError();
  return {
    schema_version: 1,
    run_id: text(item.run_id),
    editable: bool(item.editable),
    initialization_authorized: bool(item.initialization_authorized),
    blocker_codes: strings(item.blocker_codes),
    current_story_revision_id: nullableText(item.current_story_revision_id),
    current_scene_plan_collection_revision_id: nullableText(
      item.current_scene_plan_collection_revision_id,
    ),
    collection:
      item.collection === null ? null : validateCompositionCollection(item.collection),
    process_local: true,
    render_authority: false,
    renderer_execution_authority: false,
    video_render_authority: false,
    timeline_execution_authority: false,
    final_media_capability: false,
    narration_generation_capability: false,
    tts_capability: false,
  };
}

export function validateCompositionOperation(
  value: unknown,
): CompositionOperationResultV1 {
  const item = record(value);
  schemaOne(item);
  const error =
    item.error === null
      ? null
      : (() => {
          const issue = record(item.error);
          schemaOne(issue);
          if (issue.retryable !== false) throw new ProductionContractError();
          return {
            schema_version: 1 as const,
            code: text(issue.code),
            message: text(issue.message),
            retryable: false as const,
          };
        })();
  const access =
    item.access === undefined || item.access === null
      ? null
      : validateCompositionAccess(item.access);
  const collection =
    item.collection === undefined || item.collection === null
      ? null
      : validateCompositionCollection(item.collection);
  if ((access === null && collection === null) === (error === null)) {
    throw new ProductionContractError();
  }
  return { schema_version: 1, access, collection, error };
}
