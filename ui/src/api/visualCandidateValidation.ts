import type {
  VisualCandidateAccessV1,
  VisualCandidateCollectionV1,
  VisualCandidateOperationResultV1,
  VisualCandidateStatusV1,
  VisualCandidateV1,
  VisualRejectionReasonV1,
} from "../contracts/v1/production";
import { ProductionContractError } from "./productionClient";

const sha256 = /^[0-9a-f]{64}$/;
const candidateId = /^visual-candidate-[0-9]{4}-[0-9]{2}$/;
const candidateRevisionId = /^visual-candidate-revision-[0-9]{4}$/;
const collectionId = /^visual-collection-[0-9]{4}$/;
const collectionRevisionId = /^visual-collection-revision-[0-9]{4}$/;
const requestId = /^visual-request-[0-9]{4}$/;
const attemptId = /^visual-attempt-[0-9]{4}$/;
const reviewId = /^visual-review-[0-9]{4}$/;
const statuses = new Set<VisualCandidateStatusV1>([
  "REVIEW_PENDING",
  "ACCEPTED_FOR_COMPOSITION_PLANNING",
  "REJECTED",
  "REVISION_REQUESTED",
  "INVALID",
  "SUPERSEDED",
]);
const rejectionReasons = new Set<VisualRejectionReasonV1>([
  "CHARACTER_IDENTITY_MISMATCH",
  "POSE_MISMATCH",
  "ACTION_MISMATCH",
  "ENVIRONMENT_MISMATCH",
  "OBJECT_MISMATCH",
  "COMPOSITION_MISMATCH",
  "STYLE_MISMATCH",
  "CONTINUITY_MISMATCH",
  "TEXT_OR_WATERMARK_PRESENT",
  "INVALID_ANATOMY",
  "LOW_IMAGE_QUALITY",
  "DUPLICATE_CANDIDATE",
  "OTHER_BOUNDED_NOTE",
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

function strings(value: unknown): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    throw new ProductionContractError();
  }
  return [...value];
}

function schemaOne(value: Record<string, unknown>): void {
  if (value.schema_version !== 1) throw new ProductionContractError();
}

function unique(values: readonly string[]): void {
  if (new Set(values).size !== values.length) throw new ProductionContractError();
}

export function validateVisualCandidate(value: unknown): VisualCandidateV1 {
  const candidate = record(value);
  schemaOne(candidate);
  const id = text(candidate.candidate_id);
  const revision = text(candidate.candidate_revision_id);
  const revisionNumber = integer(candidate.candidate_revision_number);
  const status = text(candidate.status);
  const mime = text(candidate.mime_type);
  const extension = text(candidate.extension);
  const accepted = bool(candidate.accepted_for_composition_planning);
  if (
    !candidateId.test(id) ||
    !candidateRevisionId.test(revision) ||
    !statuses.has(status as VisualCandidateStatusV1) ||
    accepted !== (status === "ACCEPTED_FOR_COMPOSITION_PLANNING") ||
    !requestId.test(text(candidate.request_id)) ||
    !attemptId.test(text(candidate.attempt_id)) ||
    !sha256.test(text(candidate.sha256)) ||
    !sha256.test(text(candidate.logical_request_hash)) ||
    !sha256.test(text(candidate.provider_request_hash)) ||
    !["image/png", "image/jpeg", "image/webp"].includes(mime) ||
    ({ "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp" }[
      mime
    ] !== extension) ||
    !text(candidate.artifact_url).startsWith("/api/v1/plan-only/") ||
    text(candidate.artifact_url).includes("://")
  ) {
    throw new ProductionContractError();
  }
  const coverage = record(candidate.source_coverage);
  const start = integer(coverage.start, 0);
  const end = integer(coverage.end);
  if (end <= start) throw new ProductionContractError();
  const technical = record(candidate.technical_validation);
  const blockers = strings(technical.blocker_codes);
  const passed = bool(technical.passed);
  const qc = record(candidate.visual_qc);
  const qcBlockers = strings(qc.blocker_codes);
  const reviewsRaw = candidate.review_history;
  if (!Array.isArray(reviewsRaw)) throw new ProductionContractError();
  const reviews = reviewsRaw.map((item) => {
    const review = record(item);
    const reviewIdentity = text(review.review_id);
    if (
      !reviewId.test(reviewIdentity) ||
      text(review.candidate_id) !== id ||
      !candidateRevisionId.test(text(review.candidate_revision_id)) ||
      !["ACCEPTED", "REJECTED", "REVISION_REQUESTED", "SUPERSEDED"].includes(
        text(review.action),
      )
    ) {
      throw new ProductionContractError();
    }
    return {
      review_id: reviewIdentity,
      review_number: integer(review.review_number),
      candidate_id: id,
      candidate_revision_id: text(review.candidate_revision_id),
      action: text(review.action) as
        | "ACCEPTED"
        | "REJECTED"
        | "REVISION_REQUESTED"
        | "SUPERSEDED",
      reason_code: text(review.reason_code),
      note: nullableText(review.note),
      created_at: text(review.created_at),
    };
  });
  unique(reviews.map((item) => item.review_id));
  if (
    reviews.some(
      (item, index) =>
        index > 0 &&
        item.review_number <= (reviews[index - 1]?.review_number ?? 0),
    ) ||
    (reviews.length > 0 &&
      reviews.at(-1)?.candidate_revision_id !== revision)
  ) {
    throw new ProductionContractError();
  }
  if (accepted && (!passed || qcBlockers.length > 0)) {
    throw new ProductionContractError();
  }
  return {
    schema_version: 1,
    candidate_id: id,
    candidate_revision_id: revision,
    candidate_revision_number: revisionNumber,
    request_id: text(candidate.request_id),
    attempt_id: text(candidate.attempt_id),
    run_id: text(candidate.run_id),
    story_revision_id: text(candidate.story_revision_id),
    scene_plan_collection_revision_id: text(
      candidate.scene_plan_collection_revision_id,
    ),
    scene_id: text(candidate.scene_id),
    scene_revision_id: text(candidate.scene_revision_id),
    semantic_beat_id: text(candidate.semantic_beat_id),
    source_coverage: { start, end },
    status: status as VisualCandidateStatusV1,
    artifact_url: text(candidate.artifact_url),
    provider_capability_label: text(candidate.provider_capability_label),
    model_label: text(candidate.model_label),
    mime_type: mime as "image/png" | "image/jpeg" | "image/webp",
    extension: extension as ".png" | ".jpg" | ".webp",
    width: integer(candidate.width, 64),
    height: integer(candidate.height, 64),
    sha256: text(candidate.sha256),
    logical_request_hash: text(candidate.logical_request_hash),
    provider_request_hash: text(candidate.provider_request_hash),
    technical_validation: {
      passed,
      mime_valid: bool(technical.mime_valid),
      dimensions_valid: bool(technical.dimensions_valid),
      non_empty: bool(technical.non_empty),
      animation_free: bool(technical.animation_free),
      duplicate_free: bool(technical.duplicate_free),
      blocker_codes: blockers,
    },
    visual_qc: {
      blocker_codes: qcBlockers,
      warning_codes: strings(qc.warning_codes),
      information_codes: strings(qc.information_codes),
    },
    review_history: reviews,
    accepted_for_composition_planning: accepted,
    superseded_reason: nullableText(candidate.superseded_reason),
    created_at: text(candidate.created_at),
  };
}

export function validateVisualCandidateCollection(
  value: unknown,
): VisualCandidateCollectionV1 {
  const collection = record(value);
  schemaOne(collection);
  if (
    collection.visual_authority_version !== "visual_candidate_authority_v1" ||
    collection.render_authority !== false ||
    collection.video_render_authority !== false ||
    collection.final_media_capability !== false ||
    collection.narration_generation_capability !== false ||
    collection.tts_capability !== false ||
    collection.process_local !== true ||
    !collectionId.test(text(collection.visual_collection_id)) ||
    !collectionRevisionId.test(text(collection.visual_collection_revision_id)) ||
    !Array.isArray(collection.requests) ||
    !Array.isArray(collection.attempts) ||
    !Array.isArray(collection.candidates)
  ) {
    throw new ProductionContractError();
  }
  const runId = text(collection.run_id);
  const storyRevisionId = text(collection.story_revision_id);
  const sceneCollectionRevision = text(
    collection.scene_plan_collection_revision_id,
  );
  const sceneId = text(collection.scene_id);
  const sceneRevisionId = text(collection.scene_revision_id);
  const candidates = collection.candidates.map(validateVisualCandidate);
  if (
    candidates.some(
      (item) =>
        item.run_id !== runId ||
        item.story_revision_id !== storyRevisionId ||
        item.scene_plan_collection_revision_id !== sceneCollectionRevision ||
        item.scene_id !== sceneId ||
        item.scene_revision_id !== sceneRevisionId,
    )
  ) {
    throw new ProductionContractError();
  }
  unique(candidates.map((item) => item.candidate_id));
  unique(candidates.map((item) => item.candidate_revision_id));
  const allReviews = candidates
    .flatMap((item) => item.review_history)
    .sort((left, right) => left.review_number - right.review_number);
  unique(allReviews.map((item) => item.review_id));
  if (allReviews.some((item, index) => item.review_number !== index + 1)) {
    throw new ProductionContractError();
  }
  const requests = collection.requests.map((value) => {
    const request = record(value);
    const corrections = strings(request.correction_dimensions);
    if (
      !requestId.test(text(request.request_id)) ||
      request.aspect_ratio !== "9:16" ||
      !["SUCCESS", "PARTIAL_SUCCESS", "FAILED"].includes(text(request.status)) ||
      !sha256.test(text(request.logical_request_hash)) ||
      corrections.some((item) => !rejectionReasons.has(item as VisualRejectionReasonV1))
    ) {
      throw new ProductionContractError();
    }
    return {
      request_id: text(request.request_id),
      request_number: integer(request.request_number),
      visual_collection_revision_id: text(request.visual_collection_revision_id),
      candidate_count: integer(request.candidate_count),
      aspect_ratio: "9:16" as const,
      composition_emphasis: nullableText(request.composition_emphasis),
      correction_dimensions: corrections as VisualRejectionReasonV1[],
      note: nullableText(request.note),
      prompt_projection: text(request.prompt_projection),
      logical_request_hash: text(request.logical_request_hash),
      attempt_ids: strings(request.attempt_ids),
      status: text(request.status) as "SUCCESS" | "PARTIAL_SUCCESS" | "FAILED",
      created_at: text(request.created_at),
    };
  });
  const attempts = collection.attempts.map((value) => {
    const attempt = record(value);
    const status = text(attempt.status);
    const identity = text(attempt.attempt_id);
    const requestIdentity = text(attempt.request_id);
    if (
      !attemptId.test(identity) ||
      !requestId.test(requestIdentity) ||
      !["SUCCESS", "PARTIAL_SUCCESS", "FAILED"].includes(status)
    ) {
      throw new ProductionContractError();
    }
    return {
      attempt_id: identity,
      attempt_number: integer(attempt.attempt_number),
      request_id: requestIdentity,
      candidate_ids: strings(attempt.candidate_ids),
      requested_candidate_count: integer(attempt.requested_candidate_count),
      returned_candidate_count: integer(attempt.returned_candidate_count, 0),
      invalid_candidate_count: integer(attempt.invalid_candidate_count, 0),
      provider_capability_label: text(attempt.provider_capability_label),
      model_label: text(attempt.model_label),
      status: status as "SUCCESS" | "PARTIAL_SUCCESS" | "FAILED",
      reason_code: text(attempt.reason_code),
      created_at: text(attempt.created_at),
    };
  });
  unique(requests.map((item) => item.request_id));
  unique(attempts.map((item) => item.attempt_id));
  if (
    requests.some((item, index) => item.request_number !== index + 1) ||
    attempts.some((item, index) => item.attempt_number !== index + 1) ||
    attempts.some(
      (item) =>
        !requests.some((request) => request.request_id === item.request_id) ||
        item.returned_candidate_count + item.invalid_candidate_count !==
          item.requested_candidate_count ||
        item.candidate_ids.length !== item.returned_candidate_count,
    ) ||
    candidates.some(
      (item) =>
        !requests.some((request) => request.request_id === item.request_id) ||
        !attempts.some((attempt) => attempt.attempt_id === item.attempt_id),
    )
  ) {
    throw new ProductionContractError();
  }
  const accepted = candidates.filter(
    (item) => item.accepted_for_composition_planning,
  );
  const acceptedId = nullableText(collection.current_accepted_candidate_id);
  const currentRequestId = nullableText(collection.current_request_id);
  if (
    (acceptedId === null
      ? accepted.length !== 0
      : accepted.length !== 1 || accepted[0]?.candidate_id !== acceptedId) ||
    (currentRequestId !== null &&
      requests.at(-1)?.request_id !== currentRequestId)
  ) {
    throw new ProductionContractError();
  }
  const coverage = record(collection.source_coverage);
  const coverageStart = integer(coverage.start, 0);
  const coverageEnd = integer(coverage.end);
  if (coverageEnd <= coverageStart) throw new ProductionContractError();
  return {
    schema_version: 1,
    run_id: runId,
    story_revision_id: storyRevisionId,
    scene_plan_collection_revision_id: sceneCollectionRevision,
    scene_id: sceneId,
    scene_revision_id: sceneRevisionId,
    semantic_beat_id: text(collection.semantic_beat_id),
    source_coverage: {
      start: coverageStart,
      end: coverageEnd,
    },
    visual_authority_version: "visual_candidate_authority_v1",
    visual_collection_id: text(collection.visual_collection_id),
    visual_collection_revision_id: text(collection.visual_collection_revision_id),
    visual_collection_revision_number: integer(
      collection.visual_collection_revision_number,
    ),
    current_request_id: currentRequestId,
    current_accepted_candidate_id: acceptedId,
    requests,
    attempts,
    candidates,
    render_authority: false,
    video_render_authority: false,
    final_media_capability: false,
    narration_generation_capability: false,
    tts_capability: false,
    process_local: true,
    created_at: text(collection.created_at),
  };
}

export function validateVisualCandidateAccess(
  value: unknown,
): VisualCandidateAccessV1 {
  const access = record(value);
  schemaOne(access);
  if (
    access.render_authority !== false ||
    access.video_render_authority !== false ||
    access.final_media_capability !== false ||
    access.narration_generation_capability !== false ||
    access.tts_capability !== false ||
    access.process_local !== true
  ) {
    throw new ProductionContractError();
  }
  const capability = record(access.provider_capability);
  const available = bool(capability.available);
  const blockers = strings(access.blocker_codes);
  const collection =
    access.collection === null
      ? null
      : validateVisualCandidateCollection(access.collection);
  const requestAuthorized = bool(access.request_authorized);
  const reviewAuthorized = bool(access.review_authorized);
  const currentStoryRevisionId = nullableText(access.current_story_revision_id);
  const acceptedStoryRevisionId = nullableText(access.accepted_story_revision_id);
  const currentSceneCollectionRevisionId = nullableText(
    access.current_scene_plan_collection_revision_id,
  );
  const currentSceneRevisionId = nullableText(access.current_scene_revision_id);
  const currentVisualCollectionRevisionId = nullableText(
    access.current_visual_collection_revision_id,
  );
  const currentRequestId = nullableText(access.current_request_id);
  const currentAcceptedCandidateId = nullableText(
    access.current_accepted_candidate_id,
  );
  if (
    requestAuthorized !== (available && blockers.length === 0) ||
    reviewAuthorized !== (requestAuthorized && collection !== null) ||
    (requestAuthorized && currentStoryRevisionId !== acceptedStoryRevisionId) ||
    (collection !== null &&
      (collection.run_id !== text(access.run_id) ||
        collection.scene_id !== text(access.scene_id) ||
        collection.story_revision_id !== currentStoryRevisionId ||
        collection.scene_plan_collection_revision_id !==
          currentSceneCollectionRevisionId ||
        collection.scene_revision_id !== currentSceneRevisionId ||
        collection.visual_collection_revision_id !==
          currentVisualCollectionRevisionId ||
        collection.current_request_id !== currentRequestId ||
        collection.current_accepted_candidate_id !== currentAcceptedCandidateId)) ||
    (available
      ? capability.reason_code !== null
      : typeof capability.reason_code !== "string")
  ) {
    throw new ProductionContractError();
  }
  return {
    schema_version: 1,
    run_id: text(access.run_id),
    scene_id: text(access.scene_id),
    request_authorized: requestAuthorized,
    review_authorized: reviewAuthorized,
    blocker_codes: blockers,
    current_story_revision_id: currentStoryRevisionId,
    accepted_story_revision_id: acceptedStoryRevisionId,
    current_scene_plan_collection_revision_id: currentSceneCollectionRevisionId,
    current_scene_revision_id: currentSceneRevisionId,
    current_visual_collection_revision_id: currentVisualCollectionRevisionId,
    current_request_id: currentRequestId,
    current_accepted_candidate_id: currentAcceptedCandidateId,
    provider_capability: {
      available,
      capability_label: text(capability.capability_label),
      supports_9_16:
        capability.supports_9_16 === true
          ? true
          : (() => {
              throw new ProductionContractError();
            })(),
      maximum_candidate_count:
        capability.maximum_candidate_count === 4
          ? 4
          : (() => {
              throw new ProductionContractError();
            })(),
      external_provider:
        capability.external_provider === true
          ? true
          : (() => {
              throw new ProductionContractError();
            })(),
      reason_code: nullableText(capability.reason_code),
    },
    collection,
    render_authority: false,
    video_render_authority: false,
    final_media_capability: false,
    narration_generation_capability: false,
    tts_capability: false,
    process_local: true,
  };
}

export function validateVisualCandidateOperation(
  value: unknown,
): VisualCandidateOperationResultV1 {
  const operation = record(value);
  schemaOne(operation);
  const access =
    operation.access === null
      ? null
      : validateVisualCandidateAccess(operation.access);
  const rawError = operation.error;
  const error =
    rawError === null
      ? null
      : (() => {
          const item = record(rawError);
          schemaOne(item);
          return {
            schema_version: 1 as const,
            code: text(item.code),
            message: text(item.message),
            retryable: bool(item.retryable),
          };
        })();
  if ((access === null) === (error === null)) throw new ProductionContractError();
  return { schema_version: 1, access, error };
}

export function isVisualRejectionReason(
  value: string,
): value is VisualRejectionReasonV1 {
  return rejectionReasons.has(value as VisualRejectionReasonV1);
}
