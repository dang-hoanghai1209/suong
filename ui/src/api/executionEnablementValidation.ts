import type {
  ExecutionEnablementAccessV1,
  ExecutionPackageAuthorityV1,
  ExecutionPackageOperationResultV1,
  ExecutionPackageReviewEntryV1,
  ExecutionPackageReviewReasonV1,
  ExecutionPackageStatusV1,
  ExecutionPackageV1,
} from "../contracts/v1/production";
import { ProductionContractError } from "./productionClient";

const sha256 = /^[0-9a-f]{64}$/;
const identity = /^[a-z0-9][a-z0-9._-]{0,127}$/;
const statuses = new Set<ExecutionPackageStatusV1>([
  "AWAITING_CREATION",
  "SEALED_FOR_NARRATION_STAGE_REVIEW",
  "REVISION_REQUESTED",
  "CANCELLED",
  "SUPERSEDED",
]);
const reasons = new Set<ExecutionPackageReviewReasonV1>([
  "PLANNING_PACKAGE_REVIEW_REQUIRED",
  "AUTHORITY_BINDING_CONCERN",
  "NARRATION_SOURCE_CONCERN",
  "TIMELINE_CONCERN",
  "ARTIFACT_INTEGRITY_CONCERN",
  "EXECUTION_SCOPE_CONCERN",
  "LIMITATION_CONCERN",
  "OTHER_BOUNDED_NOTE",
]);
const lockKeys = [
  "full_render_enabled",
  "narration_generation_capability",
  "tts_capability",
  "audio_generation_capability",
  "audio_measurement_capability",
  "timeline_execution_authority",
  "renderer_execution_authority",
  "render_authority",
  "video_render_authority",
  "subtitle_generation_capability",
  "media_muxing_capability",
  "output_creation_capability",
  "execution_job_creation_capability",
  "final_media_capability",
] as const;
const falseLocks = {
  full_render_enabled: false,
  narration_generation_capability: false,
  tts_capability: false,
  audio_generation_capability: false,
  audio_measurement_capability: false,
  timeline_execution_authority: false,
  renderer_execution_authority: false,
  render_authority: false,
  video_render_authority: false,
  subtitle_generation_capability: false,
  media_muxing_capability: false,
  output_creation_capability: false,
  execution_job_creation_capability: false,
  final_media_capability: false,
} as const;

function fail(): never {
  throw new ProductionContractError();
}
function record(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) fail();
  return value as Record<string, unknown>;
}
function exact(item: Record<string, unknown>, keys: readonly string[]): void {
  if (
    Object.keys(item).length !== keys.length ||
    keys.some((key) => !Object.hasOwn(item, key))
  ) fail();
}
function text(value: unknown, pattern?: RegExp): string {
  if (typeof value !== "string" || (pattern && !pattern.test(value))) fail();
  return value;
}
function nullableText(value: unknown): string | null {
  return value === null ? null : text(value);
}
function integer(value: unknown, minimum = 0): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < minimum) fail();
  return value;
}
function bool(value: unknown): boolean {
  if (typeof value !== "boolean") fail();
  return value;
}
function strings(value: unknown, pattern?: RegExp): string[] {
  if (!Array.isArray(value)) fail();
  return value.map((item) => text(item, pattern));
}
function locks(item: Record<string, unknown>): void {
  if (lockKeys.some((key) => item[key] !== false)) fail();
}

function authority(value: unknown): ExecutionPackageAuthorityV1 {
  const item = record(value);
  const keys = [
    "schema_version", "readiness_report_id", "readiness_report_revision_id",
    "readiness_source_authority_sha256", "readiness_approval_id",
    "readiness_approval_purpose", "story_plan_revision_id",
    "accepted_story_plan_revision_id", "narration_source_sha256",
    "scene_collection_revision_id", "ordered_scene_ids",
    "ordered_scene_revision_ids", "visual_collection_revision_ids",
    "accepted_candidate_ids", "accepted_candidate_revision_ids",
    "accepted_candidate_artifact_sha256s", "accepted_candidate_mimes",
    "accepted_candidate_dimensions", "composition_collection_revision_id",
    "composition_ids", "composition_revision_ids",
    "timeline_collection_revision_id", "accepted_timeline_revision_id",
    "timeline_source_authority_sha256", "effective_timeline_duration_ms",
  ];
  exact(item, keys);
  if (
    item.schema_version !== 1 ||
    item.readiness_approval_purpose !== "SEPARATE_EXECUTION_ENABLEMENT_TASK_REVIEW"
  ) fail();
  const orderedSceneIds = strings(item.ordered_scene_ids, identity);
  const orderedSceneRevisionIds = strings(item.ordered_scene_revision_ids, identity);
  const visualRevisions = strings(item.visual_collection_revision_ids, identity);
  const candidateIds = strings(item.accepted_candidate_ids, identity);
  const candidateRevisions = strings(item.accepted_candidate_revision_ids, identity);
  const artifactHashes = strings(item.accepted_candidate_artifact_sha256s, sha256);
  const mimes = strings(item.accepted_candidate_mimes);
  const compositions = strings(item.composition_ids, identity);
  const compositionRevisions = strings(item.composition_revision_ids, identity);
  if (!Array.isArray(item.accepted_candidate_dimensions)) fail();
  const dimensions = item.accepted_candidate_dimensions.map((value) => {
    if (!Array.isArray(value) || value.length !== 2) fail();
    return [integer(value[0], 1), integer(value[1], 1)] as const;
  });
  const lengths = new Set([
    orderedSceneIds.length, orderedSceneRevisionIds.length, visualRevisions.length,
    candidateIds.length, candidateRevisions.length, artifactHashes.length, mimes.length,
    dimensions.length, compositions.length, compositionRevisions.length,
  ]);
  if (lengths.size !== 1 || orderedSceneIds.length === 0) fail();
  return {
    schema_version: 1,
    readiness_report_id: text(item.readiness_report_id, identity),
    readiness_report_revision_id: text(item.readiness_report_revision_id, identity),
    readiness_source_authority_sha256: text(item.readiness_source_authority_sha256, sha256),
    readiness_approval_id: text(item.readiness_approval_id, identity),
    readiness_approval_purpose: "SEPARATE_EXECUTION_ENABLEMENT_TASK_REVIEW",
    story_plan_revision_id: text(item.story_plan_revision_id, identity),
    accepted_story_plan_revision_id: text(item.accepted_story_plan_revision_id, identity),
    narration_source_sha256: text(item.narration_source_sha256, sha256),
    scene_collection_revision_id: text(item.scene_collection_revision_id, identity),
    ordered_scene_ids: orderedSceneIds,
    ordered_scene_revision_ids: orderedSceneRevisionIds,
    visual_collection_revision_ids: visualRevisions,
    accepted_candidate_ids: candidateIds,
    accepted_candidate_revision_ids: candidateRevisions,
    accepted_candidate_artifact_sha256s: artifactHashes,
    accepted_candidate_mimes: mimes,
    accepted_candidate_dimensions: dimensions,
    composition_collection_revision_id: text(item.composition_collection_revision_id, identity),
    composition_ids: compositions,
    composition_revision_ids: compositionRevisions,
    timeline_collection_revision_id: text(item.timeline_collection_revision_id, identity),
    accepted_timeline_revision_id: text(item.accepted_timeline_revision_id, identity),
    timeline_source_authority_sha256: text(item.timeline_source_authority_sha256, sha256),
    effective_timeline_duration_ms: integer(item.effective_timeline_duration_ms, 1),
  };
}

function review(value: unknown): ExecutionPackageReviewEntryV1 {
  const item = record(value);
  exact(item, [
    "schema_version", "review_id", "package_revision_id",
    "package_source_authority_sha256", "action", "reason_code", "note", "created_at",
  ]);
  if (item.schema_version !== 1) fail();
  const reason = item.reason_code === null ? null : text(item.reason_code);
  if (reason !== null && !reasons.has(reason as ExecutionPackageReviewReasonV1)) fail();
  const action = text(item.action);
  if (!["PACKAGE_CREATED", "REVISION_REQUESTED", "PACKAGE_CANCELLED", "PACKAGE_SUPERSEDED"].includes(action)) fail();
  return {
    schema_version: 1,
    review_id: text(item.review_id, identity),
    package_revision_id: text(item.package_revision_id, identity),
    package_source_authority_sha256: text(item.package_source_authority_sha256, sha256),
    action: action as ExecutionPackageReviewEntryV1["action"],
    reason_code: reason as ExecutionPackageReviewReasonV1 | null,
    note: nullableText(item.note),
    created_at: text(item.created_at),
  };
}

export function validateExecutionPackage(value: unknown): ExecutionPackageV1 {
  const item = record(value);
  exact(item, [
    "schema_version", ...lockKeys, "package_id", "package_revision_id",
    "package_revision_number", "run_id", "status", "current",
    "execution_package_created", "eligible_for_narration_stage_review",
    "package_source_authority_sha256", "authority",
    "execution_enablement_contract_version", "review_history",
    "superseded_reason", "created_at", "process_local",
  ]);
  if (
    item.schema_version !== 1 || item.execution_package_created !== true ||
    item.execution_enablement_contract_version !== "execution_enablement_v1" ||
    item.process_local !== true
  ) fail();
  locks(item);
  const status = text(item.status) as ExecutionPackageStatusV1;
  if (!statuses.has(status)) fail();
  const current = bool(item.current);
  const eligible = bool(item.eligible_for_narration_stage_review);
  if (eligible !== (current && status === "SEALED_FOR_NARRATION_STAGE_REVIEW")) fail();
  if ((status === "CANCELLED" || status === "SUPERSEDED") === current) fail();
  if (!Array.isArray(item.review_history) || item.review_history.length === 0) fail();
  const reviews = item.review_history.map(review);
  const revisionId = text(item.package_revision_id, identity);
  if (reviews.at(-1)?.package_revision_id !== revisionId) fail();
  return {
    schema_version: 1, ...falseLocks,
    package_id: text(item.package_id, identity),
    package_revision_id: revisionId,
    package_revision_number: integer(item.package_revision_number, 1),
    run_id: text(item.run_id, identity),
    status, current, execution_package_created: true,
    eligible_for_narration_stage_review: eligible,
    package_source_authority_sha256: text(item.package_source_authority_sha256, sha256),
    authority: authority(item.authority),
    execution_enablement_contract_version: "execution_enablement_v1",
    review_history: reviews,
    superseded_reason: nullableText(item.superseded_reason),
    created_at: text(item.created_at), process_local: true,
  };
}

export function validateExecutionEnablementAccess(value: unknown): ExecutionEnablementAccessV1 {
  const item = record(value);
  exact(item, [
    "schema_version", ...lockKeys, "run_id",
    "execution_package_creation_authorized", "blocker_codes",
    "current_readiness_report_id", "current_readiness_report_revision_id",
    "current_readiness_source_authority_sha256", "current_readiness_approval_id",
    "package", "package_history_count", "execution_package_created",
    "eligible_for_narration_stage_review", "process_local",
  ]);
  if (item.schema_version !== 1 || item.process_local !== true) fail();
  locks(item);
  const pkg = item.package === null ? null : validateExecutionPackage(item.package);
  const created = bool(item.execution_package_created);
  const eligible = bool(item.eligible_for_narration_stage_review);
  if (created !== (pkg !== null) || eligible !== (pkg?.eligible_for_narration_stage_review ?? false)) fail();
  return {
    schema_version: 1, ...falseLocks,
    run_id: text(item.run_id, identity),
    execution_package_creation_authorized: bool(item.execution_package_creation_authorized),
    blocker_codes: strings(item.blocker_codes),
    current_readiness_report_id: nullableText(item.current_readiness_report_id),
    current_readiness_report_revision_id: nullableText(item.current_readiness_report_revision_id),
    current_readiness_source_authority_sha256: item.current_readiness_source_authority_sha256 === null
      ? null : text(item.current_readiness_source_authority_sha256, sha256),
    current_readiness_approval_id: nullableText(item.current_readiness_approval_id),
    package: pkg, package_history_count: integer(item.package_history_count),
    execution_package_created: created,
    eligible_for_narration_stage_review: eligible, process_local: true,
  };
}

export function validateExecutionPackageOperation(value: unknown): ExecutionPackageOperationResultV1 {
  const item = record(value);
  exact(item, ["schema_version", "access", "package", "error"]);
  if (item.schema_version !== 1) fail();
  const access = item.access === null ? null : validateExecutionEnablementAccess(item.access);
  const pkg = item.package === null ? null : validateExecutionPackage(item.package);
  let error: ExecutionPackageOperationResultV1["error"] = null;
  if (item.error !== null) {
    const value = record(item.error);
    exact(value, ["schema_version", "code", "message", "retryable"]);
    if (value.schema_version !== 1 || value.retryable !== false) fail();
    error = { schema_version: 1, code: text(value.code), message: text(value.message), retryable: false };
  }
  if (error !== null && (access !== null || pkg !== null)) fail();
  return { schema_version: 1, access, package: pkg, error };
}
