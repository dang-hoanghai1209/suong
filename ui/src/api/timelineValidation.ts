import type {
  NarrationAlignmentStatusV1,
  TimelineAccessV1,
  TimelineCollectionV1,
  TimelineOperationResultV1,
  TimelineSegmentV1,
  TimelineStatusV1,
} from "../contracts/v1/production";
import { ProductionContractError } from "./productionClient";

const sha256 = /^[0-9a-f]{64}$/;
const timelineStatuses = new Set<TimelineStatusV1>([
  "VALID",
  "WARNING",
  "BLOCKED",
  "ACCEPTED_FOR_EXECUTION_REVIEW",
  "REVISION_REQUESTED",
  "SUPERSEDED",
]);
const alignmentStatuses = new Set<NarrationAlignmentStatusV1>([
  "ALIGNED",
  "WARNING",
  "BLOCKED",
  "REVISION_REQUESTED",
  "SUPERSEDED",
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
function integer(value: unknown, minimum = 0, maximum = 600_000): number {
  if (
    typeof value !== "number" ||
    !Number.isInteger(value) ||
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
function falseCapabilities(value: Record<string, unknown>): void {
  for (const key of [
    "full_render_enabled",
    "timeline_execution_authority",
    "narration_generation_capability",
    "tts_capability",
    "audio_generation_capability",
    "renderer_execution_authority",
    "render_authority",
    "video_render_authority",
    "final_media_capability",
  ]) {
    if (key in value && value[key] !== false) {
      throw new ProductionContractError("Execution capability must remain false.");
    }
  }
}
function coverage(value: unknown) {
  const item = record(value);
  const start = integer(item.start);
  const end = integer(item.end, 1);
  if (end <= start) throw new ProductionContractError();
  return { start, end };
}

export function validateTimelineSegment(value: unknown): TimelineSegmentV1 {
  const item = record(value);
  schemaOne(item);
  falseCapabilities(item);
  if (item.accepted_for_execution_review !== false) {
    throw new ProductionContractError();
  }
  const status = text(item.status) as TimelineStatusV1;
  if (
    !timelineStatuses.has(status) ||
    item.timeline_contract_version !== "timeline_planning_v1"
  ) {
    throw new ProductionContractError();
  }
  const start = integer(item.start_ms);
  const duration = integer(item.duration_ms, 1, 60_000);
  const end = integer(item.end_ms, 1);
  const transitionIn = integer(item.transition_in_ms, 0, 2_000);
  const transitionOut = integer(item.transition_out_ms, 0, 2_000);
  const effective = integer(item.effective_visible_duration_ms, 1, 60_000);
  if (
    end !== start + duration ||
    effective !== duration - transitionIn ||
    transitionIn >= duration ||
    transitionOut >= duration ||
    transitionOut > Math.floor(duration / 4)
  ) {
    throw new ProductionContractError();
  }
  const sourceCoverage = coverage(item.source_coverage);
  const narration = text(item.canonical_narration_segment);
  const alignmentRaw = record(item.narration_alignment);
  const alignmentStatus = text(
    alignmentRaw.status,
  ) as NarrationAlignmentStatusV1;
  if (
    !alignmentStatuses.has(alignmentStatus) ||
    alignmentRaw.measured_audio_alignment_available !== false ||
    alignmentRaw.tts_alignment_available !== false
  ) {
    throw new ProductionContractError();
  }
  const alignmentCoverage = coverage(alignmentRaw.source_coverage);
  const windowStart = integer(alignmentRaw.planned_window_start_ms);
  const windowEnd = integer(alignmentRaw.planned_window_end_ms, 1);
  if (
    integer(alignmentRaw.timeline_start_ms) !== start ||
    integer(alignmentRaw.timeline_end_ms, 1) !== end ||
    windowStart < start ||
    windowStart >= windowEnd ||
    windowEnd > end ||
    alignmentCoverage.start !== sourceCoverage.start ||
    alignmentCoverage.end !== sourceCoverage.end ||
    text(alignmentRaw.canonical_narration_segment) !== narration ||
    text(alignmentRaw.semantic_beat_id) !== text(item.semantic_beat_id)
  ) {
    throw new ProductionContractError();
  }
  return {
    schema_version: 1,
    segment_id: text(item.segment_id),
    segment_revision_id: text(item.segment_revision_id),
    segment_revision_number: integer(item.segment_revision_number, 1),
    position: integer(item.position, 1),
    run_id: text(item.run_id),
    story_revision_id: text(item.story_revision_id),
    narration_source_sha256: text(item.narration_source_sha256),
    scene_plan_collection_revision_id: text(
      item.scene_plan_collection_revision_id,
    ),
    scene_id: text(item.scene_id),
    scene_revision_id: text(item.scene_revision_id),
    visual_collection_revision_id: text(item.visual_collection_revision_id),
    accepted_candidate_id: text(item.accepted_candidate_id),
    accepted_candidate_revision_id: text(item.accepted_candidate_revision_id),
    candidate_artifact_sha256: text(item.candidate_artifact_sha256),
    composition_collection_revision_id: text(
      item.composition_collection_revision_id,
    ),
    composition_id: text(item.composition_id),
    composition_revision_id: text(item.composition_revision_id),
    semantic_beat_id: text(item.semantic_beat_id),
    source_coverage: sourceCoverage,
    canonical_narration_segment: narration,
    scene_planned_duration_ms: integer(
      item.scene_planned_duration_ms,
      1,
      60_000,
    ),
    duration_ms: duration,
    start_ms: start,
    end_ms: end,
    transition_in_intent: text(item.transition_in_intent),
    transition_in_ms: transitionIn,
    transition_out_intent: text(item.transition_out_intent),
    transition_out_ms: transitionOut,
    effective_visible_duration_ms: effective,
    narration_alignment: {
      alignment_id: text(alignmentRaw.alignment_id),
      canonical_narration_segment: narration,
      source_coverage: alignmentCoverage,
      semantic_beat_id: text(alignmentRaw.semantic_beat_id),
      timeline_start_ms: start,
      timeline_end_ms: end,
      planned_window_start_ms: windowStart,
      planned_window_end_ms: windowEnd,
      status: alignmentStatus,
      source_coverage_valid: bool(alignmentRaw.source_coverage_valid),
      warning_codes: strings(alignmentRaw.warning_codes),
      blocker_codes: strings(alignmentRaw.blocker_codes),
      measured_audio_alignment_available: false,
      tts_alignment_available: false,
    },
    motion_intent_summary: text(item.motion_intent_summary),
    continuity_codes: strings(item.continuity_codes),
    warning_codes: strings(item.warning_codes),
    blocker_codes: strings(item.blocker_codes),
    status,
    note: nullableText(item.note),
    changed_fields: strings(item.changed_fields),
    superseded_reason: nullableText(item.superseded_reason),
    created_at: text(item.created_at),
    timeline_contract_version: "timeline_planning_v1",
    accepted_for_execution_review: false,
    timeline_execution_authority: false,
    narration_generation_capability: false,
    tts_capability: false,
    audio_generation_capability: false,
    renderer_execution_authority: false,
    render_authority: false,
    video_render_authority: false,
    final_media_capability: false,
  };
}

export function validateTimelineCollection(
  value: unknown,
): TimelineCollectionV1 {
  const item = record(value);
  schemaOne(item);
  falseCapabilities(item);
  if (item.process_local !== true || !Array.isArray(item.segments)) {
    throw new ProductionContractError();
  }
  const segments = item.segments.map(validateTimelineSegment);
  const positions = segments.map((segment) => segment.position);
  const ids = segments.map((segment) => segment.segment_id);
  const scenes = segments.map((segment) => segment.scene_id);
  if (
    positions.some((position, index) => position !== index + 1) ||
    new Set(ids).size !== ids.length ||
    new Set(scenes).size !== scenes.length
  ) {
    throw new ProductionContractError();
  }
  const planned = segments.reduce((total, segment) => total + segment.duration_ms, 0);
  const overlap = segments
    .slice(0, -1)
    .reduce((total, segment) => total + segment.transition_out_ms, 0);
  const effective = planned - overlap;
  const ordering = segments.every(
    (segment, index) =>
      segment.start_ms ===
        (index === 0
          ? 0
          : segments[index - 1]!.end_ms -
            segments[index - 1]!.transition_out_ms) &&
      segment.transition_in_ms ===
        (index === 0 ? 0 : segments[index - 1]!.transition_out_ms),
  );
  const targetMin = integer(item.target_min_ms, 1);
  const targetMax = integer(item.target_max_ms, targetMin);
  const accepted = bool(item.accepted_for_execution_review);
  const status = text(item.status) as TimelineStatusV1;
  if (
    !timelineStatuses.has(status) ||
    integer(item.total_segment_count, 1) !== segments.length ||
    integer(item.total_planned_duration_ms, 1) !== planned ||
    integer(item.total_transition_overlap_ms) !== overlap ||
    integer(item.effective_timeline_duration_ms, 1) !== effective ||
    bool(item.ordering_valid) !== ordering ||
    bool(item.duration_valid) !== (targetMin <= effective && effective <= targetMax) ||
    accepted !== (status === "ACCEPTED_FOR_EXECUTION_REVIEW") ||
    (accepted
      ? item.accepted_timeline_revision_id !== item.collection_revision_id
      : item.accepted_timeline_revision_id !== null)
  ) {
    throw new ProductionContractError();
  }
  if (!sha256.test(text(item.narration_source_sha256)) ||
      !sha256.test(text(item.source_authority_sha256))) {
    throw new ProductionContractError();
  }
  if (!Array.isArray(item.review_history)) throw new ProductionContractError();
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
      collection_revision_id: text(review.collection_revision_id),
      created_at: text(review.created_at),
    };
  });
  return {
    schema_version: 1,
    collection_id: text(item.collection_id),
    collection_revision_id: text(item.collection_revision_id),
    collection_revision_number: integer(item.collection_revision_number, 1),
    run_id: text(item.run_id),
    story_revision_id: text(item.story_revision_id),
    narration_source_sha256: text(item.narration_source_sha256),
    source_authority_sha256: text(item.source_authority_sha256),
    scene_plan_collection_revision_id: text(item.scene_plan_collection_revision_id),
    composition_collection_revision_id: text(
      item.composition_collection_revision_id,
    ),
    segments,
    total_segment_count: segments.length,
    total_planned_duration_ms: planned,
    total_transition_overlap_ms: overlap,
    effective_timeline_duration_ms: effective,
    target_min_ms: targetMin,
    target_max_ms: targetMax,
    narration_coverage_valid: bool(item.narration_coverage_valid),
    ordering_valid: ordering,
    duration_valid: bool(item.duration_valid),
    transition_valid: bool(item.transition_valid),
    warning_segment_count: integer(item.warning_segment_count),
    blocked_segment_count: integer(item.blocked_segment_count),
    overall_ready_for_execution_review: bool(
      item.overall_ready_for_execution_review,
    ),
    status,
    accepted_for_execution_review: accepted,
    accepted_timeline_revision_id: accepted
      ? text(item.accepted_timeline_revision_id)
      : null,
    review_history: reviews,
    created_at: text(item.created_at),
    process_local: true,
    full_render_enabled: false,
    timeline_execution_authority: false,
    narration_generation_capability: false,
    tts_capability: false,
    audio_generation_capability: false,
    renderer_execution_authority: false,
    render_authority: false,
    video_render_authority: false,
    final_media_capability: false,
  };
}

export function validateTimelineAccess(value: unknown): TimelineAccessV1 {
  const item = record(value);
  schemaOne(item);
  falseCapabilities(item);
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
    current_composition_collection_revision_id: nullableText(
      item.current_composition_collection_revision_id,
    ),
    collection:
      item.collection === null ? null : validateTimelineCollection(item.collection),
    process_local: true,
    full_render_enabled: false,
    timeline_execution_authority: false,
    narration_generation_capability: false,
    tts_capability: false,
    audio_generation_capability: false,
    renderer_execution_authority: false,
    render_authority: false,
    video_render_authority: false,
    final_media_capability: false,
  };
}

export function validateTimelineOperation(
  value: unknown,
): TimelineOperationResultV1 {
  const item = record(value);
  schemaOne(item);
  const access =
    item.access === null ? null : validateTimelineAccess(item.access);
  const collection =
    item.collection === null ? null : validateTimelineCollection(item.collection);
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
  if ((access === null && collection === null) === (error === null)) {
    throw new ProductionContractError();
  }
  return { schema_version: 1, access, collection, error };
}
