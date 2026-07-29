import type {
  ExecutionReadinessAccessV1,
  ExecutionReadinessOperationResultV1,
  ExecutionReadinessReportV1,
  ReadinessAcknowledgementV1,
  ReadinessApprovalV1,
  ReadinessAuthorityBindingsV1,
  ReadinessCapabilityLocksV1,
  ReadinessLimitationV1,
  ReadinessSceneCheckV1,
  ReadinessStageCheckV1,
  ReadinessStatusV1,
} from "../contracts/v1/production";
import { ProductionContractError } from "./productionClient";

const sha256 = /^[0-9a-f]{64}$/;
const stageOrder = [
  "RUN",
  "STORY_PLAN",
  "SCENE_PLANNING",
  "VISUAL_CANDIDATES",
  "COMPOSITION_PLANNING",
  "TIMELINE_PLANNING",
  "NARRATION_SOURCE",
  "ARTIFACT_INTEGRITY",
  "AUTHORITY_FRESHNESS",
  "EXECUTION_CAPABILITY_LOCKS",
  "PROCESS_LOCAL_LIMITATIONS",
] as const;
const limitationOrder = [
  "PROCESS_LOCAL_AUTHORITY",
  "HOST_RESTART_RESETS_REVIEW_STATE",
  "REAL_PROVIDER_RUNTIME_NOT_EVALUATED",
  "TTS_NOT_GENERATED",
  "AUDIO_DURATION_NOT_MEASURED",
  "AUDIO_ALIGNMENT_NOT_VERIFIED",
  "RENDERER_NOT_EXECUTED",
  "FFMPEG_NOT_PROBED",
  "FRAME_OUTPUT_NOT_VERIFIED",
  "VIDEO_OUTPUT_NOT_VERIFIED",
  "COLOR_ACCURACY_NOT_VERIFIED",
  "MOTION_SMOOTHNESS_NOT_VERIFIED",
  "TRANSITION_RENDER_NOT_VERIFIED",
  "FINAL_MEDIA_NOT_CREATED",
  "OUTPUT_STORAGE_NOT_EVALUATED",
] as const;
const readinessStatuses = new Set<ReadinessStatusV1>([
  "LOCKED",
  "NOT_READY",
  "READY_FOR_EXECUTION_ENABLEMENT_REVIEW",
  "REVIEW_APPROVED_FOR_SEPARATE_TASK",
  "REVISION_REQUESTED",
  "REVIEW_REJECTED",
  "SUPERSEDED",
]);
const stageStatuses = new Set([
  "PASS",
  "WARNING",
  "BLOCKED",
  "NOT_EVALUATED",
  "SUPERSEDED",
]);

function record(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new ProductionContractError();
  }
  return value as Record<string, unknown>;
}
function schemaOne(value: Record<string, unknown>): void {
  if (value.schema_version !== 1) throw new ProductionContractError();
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
function integer(value: unknown, minimum = 0): number {
  if (
    typeof value !== "number" ||
    !Number.isInteger(value) ||
    value < minimum
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
function records(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) throw new ProductionContractError();
  return value.map(record);
}
function hash(value: unknown): string {
  const result = text(value);
  if (!sha256.test(result)) throw new ProductionContractError();
  return result;
}
function unique(values: readonly string[]): void {
  if (new Set(values).size !== values.length) throw new ProductionContractError();
}

const lockKeys = [
  "full_render_enabled",
  "render_authority",
  "renderer_execution_authority",
  "video_render_authority",
  "timeline_execution_authority",
  "narration_generation_capability",
  "tts_capability",
  "audio_generation_capability",
  "subtitle_generation_capability",
  "media_muxing_capability",
  "final_media_capability",
  "output_creation_capability",
  "execution_job_creation_capability",
] as const;

function capabilityLocks(value: Record<string, unknown>): ReadinessCapabilityLocksV1 {
  for (const key of lockKeys) {
    if (value[key] !== false) {
      throw new ProductionContractError("Execution capability must remain false.");
    }
  }
  return Object.fromEntries(lockKeys.map((key) => [key, false])) as unknown as ReadinessCapabilityLocksV1;
}

function stageCheck(value: unknown): ReadinessStageCheckV1 {
  const item = record(value);
  schemaOne(item);
  const status = text(item.status);
  if (!stageStatuses.has(status)) throw new ProductionContractError();
  const blockers = strings(item.blocker_codes);
  const warnings = strings(item.warning_codes);
  const information = strings(item.informational_codes);
  unique([...blockers, ...warnings, ...information]);
  if ((status === "BLOCKED") !== (blockers.length > 0)) {
    throw new ProductionContractError();
  }
  return {
    schema_version: 1,
    check_id: text(item.check_id),
    stage: text(item.stage),
    status: status as ReadinessStageCheckV1["status"],
    blocker_codes: blockers,
    warning_codes: warnings,
    informational_codes: information,
    bound_revision_ids: strings(item.bound_revision_ids),
    summary: text(item.summary),
    required_for_review_eligibility: bool(
      item.required_for_review_eligibility,
    ),
    checked_at: text(item.checked_at),
  };
}

function sceneCheck(value: unknown): ReadinessSceneCheckV1 {
  const item = record(value);
  schemaOne(item);
  const coverage = record(item.source_coverage);
  schemaOne(coverage);
  const start = integer(coverage.start);
  const end = integer(coverage.end, 1);
  const startMs = integer(item.start_ms);
  const endMs = integer(item.end_ms, 1);
  const durationMs = integer(item.duration_ms, 1);
  const transition = integer(item.transition_duration_ms);
  const blockers = strings(item.blocker_codes);
  if (
    end <= start ||
    endMs !== startMs + durationMs ||
    transition >= durationMs ||
    blockers.length ||
    item.artifact_registered !== true ||
    item.artifact_technically_valid !== true ||
    item.composition_accepted !== true ||
    item.narration_alignment_status !== "ALIGNED" ||
    item.readiness_status !== "PASS"
  ) {
    throw new ProductionContractError();
  }
  return {
    schema_version: 1,
    scene_check_id: text(item.scene_check_id),
    position: integer(item.position, 1),
    scene_id: text(item.scene_id),
    scene_revision_id: text(item.scene_revision_id),
    semantic_beat_id: text(item.semantic_beat_id),
    source_coverage: { schema_version: 1, start, end },
    visual_collection_revision_id: text(item.visual_collection_revision_id),
    accepted_candidate_id: text(item.accepted_candidate_id),
    candidate_revision_id: text(item.candidate_revision_id),
    artifact_sha256: hash(item.artifact_sha256),
    artifact_mime: text(item.artifact_mime),
    artifact_width: integer(item.artifact_width, 1),
    artifact_height: integer(item.artifact_height, 1),
    artifact_registered: true,
    artifact_technically_valid: true,
    composition_id: text(item.composition_id),
    composition_revision_id: text(item.composition_revision_id),
    composition_accepted: true,
    timeline_segment_id: text(item.timeline_segment_id),
    timeline_segment_revision_id: text(item.timeline_segment_revision_id),
    start_ms: startMs,
    end_ms: endMs,
    duration_ms: durationMs,
    transition_duration_ms: transition,
    narration_alignment_status: "ALIGNED",
    blocker_codes: blockers,
    warning_codes: strings(item.warning_codes),
    readiness_status: "PASS",
  };
}

function acknowledgement(value: unknown): ReadinessAcknowledgementV1 {
  const item = record(value);
  schemaOne(item);
  if (item.acknowledged !== true) throw new ProductionContractError();
  return {
    schema_version: 1,
    acknowledgement_id: text(item.acknowledgement_id),
    report_revision_id: text(item.report_revision_id),
    source_authority_sha256: hash(item.source_authority_sha256),
    limitation_code: text(item.limitation_code),
    acknowledged: true,
    reviewer_note: nullableText(item.reviewer_note),
    created_at: text(item.created_at),
  };
}

function limitation(value: unknown): ReadinessLimitationV1 {
  const item = record(value);
  schemaOne(item);
  const acknowledged = bool(item.acknowledged);
  const acknowledgementId = nullableText(item.acknowledgement_id);
  if (
    item.acknowledgement_required !== true ||
    acknowledged !== (acknowledgementId !== null)
  ) {
    throw new ProductionContractError();
  }
  return {
    schema_version: 1,
    limitation_code: text(item.limitation_code),
    description: text(item.description),
    acknowledgement_required: true,
    acknowledged,
    acknowledgement_id: acknowledgementId,
  };
}

function authority(value: unknown): ReadinessAuthorityBindingsV1 {
  const item = record(value);
  schemaOne(item);
  const visualRevisions = strings(item.visual_collection_revision_ids);
  const candidateIds = strings(item.accepted_candidate_ids);
  const candidateRevisions = strings(item.accepted_candidate_revision_ids);
  const artifactHashes = strings(item.accepted_candidate_artifact_sha256s);
  const compositionIds = strings(item.composition_ids);
  const compositionRevisions = strings(item.composition_revision_ids);
  const lengths = new Set([
    visualRevisions.length,
    candidateIds.length,
    candidateRevisions.length,
    artifactHashes.length,
    compositionIds.length,
    compositionRevisions.length,
  ]);
  if (lengths.size !== 1 || visualRevisions.length === 0) {
    throw new ProductionContractError();
  }
  artifactHashes.forEach((item) => {
    if (!sha256.test(item)) throw new ProductionContractError();
  });
  return {
    schema_version: 1,
    story_plan_revision_id: text(item.story_plan_revision_id),
    accepted_story_plan_revision_id: text(
      item.accepted_story_plan_revision_id,
    ),
    narration_source_sha256: hash(item.narration_source_sha256),
    scene_collection_revision_id: text(item.scene_collection_revision_id),
    visual_collection_revision_ids: visualRevisions,
    accepted_candidate_ids: candidateIds,
    accepted_candidate_revision_ids: candidateRevisions,
    accepted_candidate_artifact_sha256s: artifactHashes,
    composition_collection_revision_id: text(
      item.composition_collection_revision_id,
    ),
    composition_ids: compositionIds,
    composition_revision_ids: compositionRevisions,
    timeline_collection_revision_id: text(
      item.timeline_collection_revision_id,
    ),
    accepted_timeline_revision_id: text(item.accepted_timeline_revision_id),
    timeline_source_authority_sha256: hash(
      item.timeline_source_authority_sha256,
    ),
  };
}

function approval(value: unknown): ReadinessApprovalV1 {
  const item = record(value);
  schemaOne(item);
  const locks = capabilityLocks(item);
  if (
    item.purpose !== "SEPARATE_EXECUTION_ENABLEMENT_TASK_REVIEW" ||
    item.approved_for_separate_execution_enablement_review !== true
  ) {
    throw new ProductionContractError();
  }
  return {
    ...locks,
    schema_version: 1,
    approval_id: text(item.approval_id),
    report_revision_id: text(item.report_revision_id),
    source_authority_sha256: hash(item.source_authority_sha256),
    purpose: "SEPARATE_EXECUTION_ENABLEMENT_TASK_REVIEW",
    approved_for_separate_execution_enablement_review: true,
    reviewer_note: nullableText(item.reviewer_note),
    created_at: text(item.created_at),
  };
}

export function validateExecutionReadinessReport(
  value: unknown,
): ExecutionReadinessReportV1 {
  const item = record(value);
  schemaOne(item);
  const locks = capabilityLocks(item);
  const status = text(item.status);
  if (!readinessStatuses.has(status as ReadinessStatusV1)) {
    throw new ProductionContractError();
  }
  const stages = records(item.stage_checks).map(stageCheck);
  if (
    stages.length !== stageOrder.length ||
    stages.some((stage, index) => stage.stage !== stageOrder[index])
  ) {
    throw new ProductionContractError();
  }
  unique(stages.map((stage) => stage.check_id));
  const scenes = records(item.scene_checks).map(sceneCheck);
  if (scenes.some((scene, index) => scene.position !== index + 1)) {
    throw new ProductionContractError();
  }
  unique(scenes.map((scene) => scene.scene_check_id));
  unique(scenes.map((scene) => scene.scene_id));
  const limitations = records(item.limitations).map(limitation);
  if (
    limitations.length !== limitationOrder.length ||
    limitations.some(
      (entry, index) => entry.limitation_code !== limitationOrder[index],
    )
  ) {
    throw new ProductionContractError();
  }
  const acknowledgements = records(item.acknowledgements).map(acknowledgement);
  unique(acknowledgements.map((entry) => entry.acknowledgement_id));
  const reportApproval =
    item.approval === null ? null : approval(item.approval);
  const blockers =
    stages.reduce((sum, entry) => sum + entry.blocker_codes.length, 0) +
    scenes.reduce((sum, entry) => sum + entry.blocker_codes.length, 0);
  const warnings =
    stages.reduce((sum, entry) => sum + entry.warning_codes.length, 0) +
    scenes.reduce((sum, entry) => sum + entry.warning_codes.length, 0);
  const information = stages.reduce(
    (sum, entry) => sum + entry.informational_codes.length,
    0,
  );
  const current = bool(item.current);
  const structurallyReady =
    blockers === 0 &&
    stages
      .filter((entry) => entry.required_for_review_eligibility)
      .every((entry) => entry.status === "PASS");
  const planningReady =
    structurallyReady && current && status !== "SUPERSEDED";
  const reviewEligible =
    planningReady &&
    (status === "READY_FOR_EXECUTION_ENABLEMENT_REVIEW" ||
      status === "REVIEW_APPROVED_FOR_SEPARATE_TASK");
  if (
    integer(item.blocker_count) !== blockers ||
    integer(item.warning_count) !== warnings ||
    integer(item.informational_count) !== information ||
    bool(item.approved_for_separate_execution_enablement_review) !==
      (reportApproval !== null) ||
    bool(item.planning_package_ready) !== planningReady ||
    bool(item.future_execution_enablement_review_eligible) !==
      reviewEligible ||
    (status === "SUPERSEDED") !== !current ||
    (status === "REVIEW_APPROVED_FOR_SEPARATE_TASK") !==
      (reportApproval !== null) ||
    item.process_local !== true ||
    item.narration_coverage_valid !== true ||
    item.transition_valid !== true ||
    item.target_duration_valid !== true
  ) {
    throw new ProductionContractError();
  }
  const reviews = records(item.review_history).map((entry) => {
    schemaOne(entry);
    return {
      schema_version: 1 as const,
      review_id: text(entry.review_id),
      report_revision_id: text(entry.report_revision_id),
      source_authority_sha256: hash(entry.source_authority_sha256),
      action: text(entry.action),
      reason_code: nullableText(entry.reason_code),
      note: nullableText(entry.note),
      created_at: text(entry.created_at),
    };
  });
  return {
    ...locks,
    schema_version: 1,
    report_id: text(item.report_id),
    report_revision_id: text(item.report_revision_id),
    report_revision_number: integer(item.report_revision_number, 1),
    run_id: text(item.run_id),
    source_authority_sha256: hash(item.source_authority_sha256),
    status: status as ReadinessStatusV1,
    current,
    planning_package_ready: planningReady,
    future_execution_enablement_review_eligible: reviewEligible,
    approved_for_separate_execution_enablement_review: bool(
      item.approved_for_separate_execution_enablement_review,
    ),
    blocker_count: blockers,
    warning_count: warnings,
    informational_count: information,
    stage_checks: stages,
    scene_checks: scenes,
    limitations,
    acknowledgements,
    approval: reportApproval,
    review_history: reviews,
    authority: authority(item.authority),
    total_effective_timeline_duration_ms: integer(
      item.total_effective_timeline_duration_ms,
      1,
    ),
    narration_coverage_valid: true,
    transition_valid: true,
    target_duration_valid: true,
    process_local: true,
    created_at: text(item.created_at),
  };
}

export function validateExecutionReadinessAccess(
  value: unknown,
): ExecutionReadinessAccessV1 {
  const item = record(value);
  schemaOne(item);
  const locks = capabilityLocks(item);
  if (item.process_local !== true) throw new ProductionContractError();
  return {
    ...locks,
    schema_version: 1,
    run_id: text(item.run_id),
    review_available: bool(item.review_available),
    initialization_authorized: bool(item.initialization_authorized),
    mutation_authorized: bool(item.mutation_authorized),
    blocker_codes: strings(item.blocker_codes),
    report:
      item.report === null ? null : validateExecutionReadinessReport(item.report),
    process_local: true,
  };
}

export function validateExecutionReadinessOperation(
  value: unknown,
): ExecutionReadinessOperationResultV1 {
  const item = record(value);
  schemaOne(item);
  const error =
    item.error === null
      ? null
      : (() => {
          const result = record(item.error);
          schemaOne(result);
          if (result.retryable !== false) throw new ProductionContractError();
          return {
            schema_version: 1 as const,
            code: text(result.code),
            message: text(result.message),
            retryable: false as const,
          };
        })();
  const access =
    item.access === null ? null : validateExecutionReadinessAccess(item.access);
  const report =
    item.report === null ? null : validateExecutionReadinessReport(item.report);
  if ((error === null) === (report === null && access === null)) {
    throw new ProductionContractError();
  }
  return { schema_version: 1, access, report, error };
}
