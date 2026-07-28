import type {
  AcceptStoryPlanRequestV1,
  PlanOnlyCreateRequestV1,
  PlanOnlyCreateResultV1,
  PlanOnlyHealthV1,
  ProductionCapabilitiesV1,
  ProductionDashboardViewV1,
  ProductionRunViewV1,
  PublicApiErrorV1,
  ReplanRequestV1,
  DuplicateSceneRequestV1,
  MergeScenesRequestV1,
  ReorderSceneRequestV1,
  RequestSceneRevisionV1,
  RestoreSceneRevisionRequestV1,
  SaveSceneRevisionRequestV1,
  ScenePlanAccessV1,
  ScenePlanCollectionHistoryV1,
  ScenePlanCollectionV1,
  ScenePlanOperationResultV1,
  ScenePlanSceneHistoryV1,
  ScenePlanV1,
  SceneTransitionRequestV1,
  SplitSceneRequestV1,
  StoryPlanReviewOperationResultV1,
  StoryPlanReviewViewV1,
  StoryPlanRevisionHistoryV1,
  StoryPlanRevisionV1,
  GenerateVisualCandidatesRequestV1,
  VisualCandidateAccessV1,
  VisualCandidateMutationRequestV1,
  VisualCandidateOperationResultV1,
  VisualCandidateV1,
  CompositionAccessV1,
  CompositionEditV1,
  CompositionOperationResultV1,
  CompositionReviewReasonV1,
  TimelineAccessV1,
  TimelineOperationResultV1,
  TimelineReviewReasonV1,
} from "../contracts/v1/production";
import {
  validateCompositionAccess,
  validateCompositionOperation,
  validateSceneComposition,
} from "./compositionValidation";
import {
  validateTimelineAccess,
  validateTimelineOperation,
  validateTimelineSegment,
} from "./timelineValidation";
import {
  validateSceneHistory,
  validateScenePlanCollection,
  validateScenePlanCollectionHistory,
  validateScenePlan,
  validateScenePlanAccess,
  validateScenePlanOperation,
} from "./scenePlanValidation";
import {
  validateVisualCandidate,
  validateVisualCandidateAccess,
  validateVisualCandidateOperation,
} from "./visualCandidateValidation";

const apiPrefix = "/api/v1/plan-only";

export interface ProductionRepository {
  createPlanOnlyRun(request: PlanOnlyCreateRequestV1): Promise<PlanOnlyCreateResultV1>;
  getDashboard(): Promise<ProductionDashboardViewV1>;
  getCapabilities(): Promise<ProductionCapabilitiesV1>;
  getHealth(): Promise<PlanOnlyHealthV1>;
  getRun(runId: string): Promise<ProductionRunViewV1 | null>;
  getStoryPlanReview(runId: string): Promise<StoryPlanReviewViewV1 | null>;
  acceptStoryPlan(
    runId: string,
    request: AcceptStoryPlanRequestV1,
  ): Promise<StoryPlanReviewOperationResultV1>;
  requestStoryPlanReplan(
    runId: string,
    request: ReplanRequestV1,
  ): Promise<StoryPlanReviewOperationResultV1>;
  getStoryPlanRevisions(runId: string): Promise<StoryPlanRevisionHistoryV1 | null>;
  getStoryPlanRevision(
    runId: string,
    revisionId: string,
  ): Promise<StoryPlanRevisionV1 | null>;
  getScenePlan?(runId: string): Promise<ScenePlanAccessV1 | null>;
  initializeScenePlan?(
    runId: string,
    acceptedStoryRevisionId: string,
  ): Promise<ScenePlanOperationResultV1>;
  saveSceneRevision?(
    runId: string,
    sceneId: string,
    request: SaveSceneRevisionRequestV1,
  ): Promise<ScenePlanOperationResultV1>;
  reorderScene?(
    runId: string,
    request: ReorderSceneRequestV1,
  ): Promise<ScenePlanOperationResultV1>;
  splitScene?(
    runId: string,
    request: SplitSceneRequestV1,
  ): Promise<ScenePlanOperationResultV1>;
  mergeScenes?(
    runId: string,
    request: MergeScenesRequestV1,
  ): Promise<ScenePlanOperationResultV1>;
  duplicateScene?(
    runId: string,
    request: DuplicateSceneRequestV1,
  ): Promise<ScenePlanOperationResultV1>;
  acceptScene?(
    runId: string,
    sceneId: string,
    request: SceneTransitionRequestV1,
  ): Promise<ScenePlanOperationResultV1>;
  requestSceneRevision?(
    runId: string,
    sceneId: string,
    request: RequestSceneRevisionV1,
  ): Promise<ScenePlanOperationResultV1>;
  restoreSceneRevision?(
    runId: string,
    sceneId: string,
    request: RestoreSceneRevisionRequestV1,
  ): Promise<ScenePlanOperationResultV1>;
  getPlannedScene?(runId: string, sceneId: string): Promise<ScenePlanV1 | null>;
  getSceneHistory?(
    runId: string,
    sceneId: string,
  ): Promise<ScenePlanSceneHistoryV1 | null>;
  getScenePlanRevisions?(
    runId: string,
  ): Promise<ScenePlanCollectionHistoryV1 | null>;
  getScenePlanCollectionRevision?(
    runId: string,
    revisionId: string,
  ): Promise<ScenePlanCollectionV1 | null>;
  getVisualCandidateAccess?(
    runId: string,
    sceneId: string,
  ): Promise<VisualCandidateAccessV1 | null>;
  generateVisualCandidates?(
    runId: string,
    sceneId: string,
    request: GenerateVisualCandidatesRequestV1,
  ): Promise<VisualCandidateOperationResultV1>;
  getVisualCandidate?(
    runId: string,
    sceneId: string,
    candidateId: string,
  ): Promise<VisualCandidateV1 | null>;
  mutateVisualCandidate?(
    runId: string,
    sceneId: string,
    candidateId: string,
    operation: "accept" | "reject" | "request-revision",
    request: VisualCandidateMutationRequestV1,
  ): Promise<VisualCandidateOperationResultV1>;
  getCompositionAccess?(runId: string): Promise<CompositionAccessV1 | null>;
  initializeCompositions?(
    runId: string,
    scenePlanCollectionRevisionId: string,
  ): Promise<CompositionOperationResultV1>;
  saveComposition?(
    runId: string,
    sceneId: string,
    request: {
      readonly schema_version: 1;
      readonly base_collection_revision_id: string;
      readonly base_composition_revision_id: string;
      readonly changes: CompositionEditV1;
    },
  ): Promise<CompositionOperationResultV1>;
  reviewComposition?(
    runId: string,
    sceneId: string,
    operation: "accept" | "request-revision",
    request: {
      readonly schema_version: 1;
      readonly base_collection_revision_id: string;
      readonly base_composition_revision_id: string;
      readonly reason_code: CompositionReviewReasonV1;
      readonly note: string | null;
    },
  ): Promise<CompositionOperationResultV1>;
  restoreComposition?(
    runId: string,
    sceneId: string,
    request: {
      readonly schema_version: 1;
      readonly base_collection_revision_id: string;
      readonly base_composition_revision_id: string;
      readonly restore_composition_revision_id: string;
      readonly reason: string;
    },
  ): Promise<CompositionOperationResultV1>;
  getCompositionHistory?(
    runId: string,
    sceneId: string,
  ): Promise<{
    readonly schema_version: 1;
    readonly run_id: string;
    readonly scene_id: string;
    readonly revisions: readonly import("../contracts/v1/production").SceneCompositionV1[];
  } | null>;
  getTimelineAccess?(runId: string): Promise<TimelineAccessV1 | null>;
  initializeTimeline?(
    runId: string,
    compositionCollectionRevisionId: string,
  ): Promise<TimelineOperationResultV1>;
  saveTimelineSegment?(
    runId: string,
    segmentId: string,
    request: {
      readonly schema_version: 1;
      readonly base_collection_revision_id: string;
      readonly base_segment_revision_id: string;
      readonly changes: {
        readonly narration_window_start_ms?: number;
        readonly narration_window_end_ms?: number;
        readonly note?: string | null;
      };
    },
  ): Promise<TimelineOperationResultV1>;
  restoreTimelineSegment?(
    runId: string,
    segmentId: string,
    request: {
      readonly schema_version: 1;
      readonly base_collection_revision_id: string;
      readonly base_segment_revision_id: string;
      readonly restore_segment_revision_id: string;
      readonly reason: string;
    },
  ): Promise<TimelineOperationResultV1>;
  reviewTimeline?(
    runId: string,
    operation: "accept" | "request-revision",
    request: {
      readonly schema_version: 1;
      readonly base_collection_revision_id: string;
      readonly reason_code: TimelineReviewReasonV1;
      readonly note: string | null;
    },
  ): Promise<TimelineOperationResultV1>;
  getTimelineSegmentHistory?(
    runId: string,
    segmentId: string,
  ): Promise<{
    readonly schema_version: 1;
    readonly run_id: string;
    readonly segment_id: string;
    readonly revisions: readonly import("../contracts/v1/production").TimelineSegmentV1[];
  } | null>;
}

export class ProductionContractError extends Error {
  constructor(message = "The backend returned an invalid production response.") {
    super(message);
    this.name = "ProductionContractError";
  }
}

export class ProductionBackendUnavailableError extends Error {
  constructor() {
    super("The PLAN_ONLY backend is unavailable.");
    this.name = "ProductionBackendUnavailableError";
  }
}

interface TransportResponse {
  readonly status: number;
  readonly payload: unknown;
}

export interface PlanOnlyTransport {
  request(path: string, init?: RequestInit): Promise<TransportResponse>;
}

export function defineNativeFetchClient(
  fetchImplementation: typeof fetch,
): PlanOnlyTransport {
  return {
    async request(path: string, init?: RequestInit): Promise<TransportResponse> {
      const method = init?.method ?? "GET";
      const exactCollection = path === `${apiPrefix}/runs`;
      const exactCapabilities =
        method === "GET" && path === `${apiPrefix}/capabilities`;
      const exactHealth = method === "GET" && path === `${apiPrefix}/health`;
      const exactLookup =
        method === "GET" &&
        path.startsWith(`${apiPrefix}/runs/`) &&
        path.slice(`${apiPrefix}/runs/`.length).length > 0 &&
        !path.slice(`${apiPrefix}/runs/`.length).includes("/");
      const runParts = path.startsWith(`${apiPrefix}/runs/`)
        ? path.slice(`${apiPrefix}/runs/`.length).split("/")
        : [];
      const exactReview =
        method === "GET" &&
        runParts.length === 2 &&
        runParts[0] !== "" &&
        runParts[1] === "review";
      const exactAccept =
        method === "POST" &&
        runParts.length === 3 &&
        runParts[0] !== "" &&
        runParts[1] === "review" &&
        runParts[2] === "accept";
      const exactRevisions =
        (method === "GET" || method === "POST") &&
        runParts.length === 2 &&
        runParts[0] !== "" &&
        runParts[1] === "revisions";
      const exactRevision =
        method === "GET" &&
        runParts.length === 3 &&
        runParts[0] !== "" &&
        runParts[1] === "revisions" &&
        runParts[2] !== "";
      const sceneTail =
        runParts.length >= 2 && runParts[1] === "scene-plan"
          ? runParts.slice(2)
          : [];
      const exactScenePlan =
        runParts.length >= 2 &&
        runParts[0] !== "" &&
        runParts[1] === "scene-plan" &&
        ((method === "GET" && sceneTail.length === 0) ||
          (method === "POST" &&
            sceneTail.length === 1 &&
            [
              "initialize",
              "reorder",
              "split",
              "merge",
              "duplicate",
            ].includes(sceneTail[0] ?? "")) ||
          (method === "GET" &&
            sceneTail.length === 1 &&
            sceneTail[0] === "revisions") ||
          (method === "GET" &&
            sceneTail.length === 2 &&
            sceneTail[0] === "revisions" &&
            sceneTail[1] !== "") ||
          (method === "GET" &&
            sceneTail.length === 2 &&
            sceneTail[0] === "scenes" &&
            sceneTail[1] !== "") ||
          (method === "GET" &&
            sceneTail.length === 3 &&
            sceneTail[0] === "scenes" &&
            sceneTail[1] !== "" &&
            sceneTail[2] === "history") ||
          (method === "POST" &&
            sceneTail.length === 3 &&
            sceneTail[0] === "scenes" &&
            sceneTail[1] !== "" &&
            ["revisions", "restore", "accept", "request-revision"].includes(
              sceneTail[2] ?? "",
            )) ||
          (method === "GET" &&
            sceneTail.length === 3 &&
            sceneTail[0] === "scenes" &&
            sceneTail[1] !== "" &&
            sceneTail[2] === "visual-candidates") ||
          (method === "POST" &&
            sceneTail.length === 4 &&
            sceneTail[0] === "scenes" &&
            sceneTail[1] !== "" &&
            sceneTail[2] === "visual-candidates" &&
            sceneTail[3] === "generate") ||
          (method === "GET" &&
            sceneTail.length === 4 &&
            sceneTail[0] === "scenes" &&
            sceneTail[1] !== "" &&
            sceneTail[2] === "visual-candidates" &&
            sceneTail[3] !== "") ||
          (method === "POST" &&
            sceneTail.length === 5 &&
            sceneTail[0] === "scenes" &&
            sceneTail[1] !== "" &&
            sceneTail[2] === "visual-candidates" &&
            sceneTail[3] !== "" &&
            ["accept", "reject", "request-revision"].includes(
              sceneTail[4] ?? "",
            )));
      const exactCreate = method === "POST" && exactCollection;
      const exactList = method === "GET" && exactCollection;
      if (
        path.includes("://") ||
        !(
          exactCapabilities ||
          exactHealth ||
          exactLookup ||
          exactCreate ||
          exactList ||
          exactReview ||
          exactAccept ||
          exactRevisions ||
          exactRevision ||
          exactScenePlan
        )
      ) {
        throw new ProductionContractError("Only the local PLAN_ONLY contract is allowed.");
      }
      let response: Response;
      try {
        response = await fetchImplementation(path, init);
      } catch {
        throw new ProductionBackendUnavailableError();
      }
      let payload: unknown = null;
      try {
        payload = await response.json();
      } catch {
        if (response.status !== 404) {
          throw new ProductionContractError();
        }
      }
      return { status: response.status, payload };
    },
  };
}

function record(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new ProductionContractError();
  }
  return value as Record<string, unknown>;
}

function stringValue(value: unknown): string {
  if (typeof value !== "string") {
    throw new ProductionContractError();
  }
  return value;
}

function finiteNumber(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new ProductionContractError();
  }
  return value;
}

function positiveNumber(value: unknown): number {
  const number = finiteNumber(value);
  if (number <= 0) {
    throw new ProductionContractError();
  }
  return number;
}

function stringArray(value: unknown): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    throw new ProductionContractError();
  }
  return [...value];
}

function requireSchemaOne(value: Record<string, unknown>): void {
  if (value.schema_version !== 1) {
    throw new ProductionContractError();
  }
}

function validateCondition(value: unknown) {
  const item = record(value);
  const severity = stringValue(item.severity);
  if (!["INFO", "WARNING", "BLOCKED", "ERROR"].includes(severity)) {
    throw new ProductionContractError();
  }
  return {
    code: stringValue(item.code),
    title: stringValue(item.title),
    detail: stringValue(item.detail),
    severity: severity as "INFO" | "WARNING" | "BLOCKED" | "ERROR",
    scene_ids: stringArray(item.scene_ids),
    beat_ids: stringArray(item.beat_ids),
  };
}

function validateRun(value: unknown): ProductionRunViewV1 {
  const run = record(value);
  requireSchemaOne(run);
  const status = stringValue(run.status);
  if (
    !["PLANNING", "PLANNED", "BLOCKED", "FAILED"].includes(status)
  ) {
    throw new ProductionContractError();
  }
  const normalized = record(run.normalized_input);
  const warnings = record(run.warnings);
  requireSchemaOne(warnings);
  if (!Array.isArray(warnings.conditions)) {
    throw new ProductionContractError();
  }
  const conditions = warnings.conditions.map(validateCondition);
  if (warnings.warning_count !== conditions.length) {
    throw new ProductionContractError();
  }
  const readiness = record(run.render_readiness);
  if (readiness.ready !== false) {
    throw new ProductionContractError();
  }
  const story = run.story_plan === null ? null : record(run.story_plan);
  const timeline = run.timeline === null ? null : record(run.timeline);
  let storyPlan: ProductionRunViewV1["story_plan"] = null;
  if (story !== null) {
    requireSchemaOne(story);
    if (!Array.isArray(story.emotional_arc) || !Array.isArray(story.semantic_beats)) {
      throw new ProductionContractError();
    }
    if (!Array.isArray(story.scenes)) {
      throw new ProductionContractError();
    }
    const semanticBeats = story.semantic_beats.map((raw, index) => {
      const beat = record(raw);
      if (beat.order !== index + 1) {
        throw new ProductionContractError();
      }
      return {
        beat_id: stringValue(beat.beat_id),
        order: finiteNumber(beat.order),
        narration_segment: stringValue(beat.narration_segment),
        semantic_purpose: stringValue(beat.semantic_purpose),
        emotional_state: stringValue(beat.emotional_state),
        visual_intent: stringValue(beat.visual_intent),
        duration_seconds: positiveNumber(beat.duration_seconds),
      };
    });
    const scenes = story.scenes.map((raw, index) => {
      const scene = record(raw);
      if (
        scene.order !== index + 1 ||
        scene.source_beat_id !== semanticBeats[index]?.beat_id
      ) {
        throw new ProductionContractError();
      }
      return {
        scene_id: stringValue(scene.scene_id),
        order: finiteNumber(scene.order),
        source_beat_id: stringValue(scene.source_beat_id),
        meaning: stringValue(scene.meaning),
        emotional_tone: stringArray(scene.emotional_tone),
        visual_intent: stringValue(scene.visual_intent),
        planned_duration_seconds: positiveNumber(scene.planned_duration_seconds),
      };
    });
    storyPlan = {
      schema_version: 1,
      topic: stringValue(story.topic),
      language: stringValue(story.language),
      aspect_ratio:
        stringValue(story.aspect_ratio) === "9:16"
          ? "9:16"
          : (() => {
              throw new ProductionContractError();
            })(),
      target_duration_seconds: positiveNumber(story.target_duration_seconds),
      narration_text: stringValue(story.narration_text),
      emotional_arc: stringArray(story.emotional_arc),
      semantic_beats: semanticBeats,
      scenes,
    };
  }
  let timelineView: ProductionRunViewV1["timeline"] = null;
  if (timeline !== null) {
    requireSchemaOne(timeline);
    if (timeline.authority !== "PLANNED" && timeline.authority !== "MEASURED") {
      throw new ProductionContractError();
    }
    if (!Array.isArray(timeline.rows)) {
      throw new ProductionContractError();
    }
    const rows = timeline.rows.map((raw, index) => {
      const row = record(raw);
      if (row.order !== index + 1) {
        throw new ProductionContractError();
      }
      return {
        scene_id: stringValue(row.scene_id),
        order: finiteNumber(row.order),
        start_seconds: finiteNumber(row.start_seconds),
        narration_slot_duration_seconds: positiveNumber(
          row.narration_slot_duration_seconds,
        ),
        render_clip_duration_seconds:
          row.render_clip_duration_seconds === null
            ? null
            : finiteNumber(row.render_clip_duration_seconds),
        end_seconds: finiteNumber(row.end_seconds),
      };
    });
    if (
      rows.length === 0 ||
      timeline.total_duration_seconds !== rows.at(-1)?.end_seconds
    ) {
      throw new ProductionContractError();
    }
    timelineView = {
      schema_version: 1,
      authority: timeline.authority,
      transition_profile_id:
        timeline.transition_profile_id === null
          ? null
          : stringValue(timeline.transition_profile_id),
      configured_transition_seconds:
        timeline.configured_transition_seconds === null
          ? null
          : finiteNumber(timeline.configured_transition_seconds),
      effective_transition_seconds:
        timeline.effective_transition_seconds === null
          ? null
          : finiteNumber(timeline.effective_transition_seconds),
      total_duration_seconds: finiteNumber(timeline.total_duration_seconds),
      rows,
    };
  }
  const mode = stringValue(run.execution_mode);
  if (mode !== "PLAN_ONLY") {
    throw new ProductionContractError();
  }
  const inputMode = stringValue(normalized.input_mode);
  if (!["TOPIC", "NARRATION", "NARRATION_WITH_SCENE_HINTS"].includes(inputMode)) {
    throw new ProductionContractError();
  }
  const runId = stringValue(run.run_id);
  const timestampPattern = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/;
  const createdAt = stringValue(run.created_at);
  const updatedAt = stringValue(run.updated_at);
  if (
    !/^plan-[0-9a-f]{20}$/.test(runId) ||
    !timestampPattern.test(createdAt) ||
    !timestampPattern.test(updatedAt) ||
    (storyPlan === null) !== (timelineView === null) ||
    (status === "PLANNED" && storyPlan === null)
  ) {
    throw new ProductionContractError();
  }
  if (
    storyPlan !== null &&
    timelineView !== null &&
    (storyPlan.scenes.length !== timelineView.rows.length ||
      storyPlan.scenes.some(
        (scene, index) => scene.scene_id !== timelineView.rows[index]?.scene_id,
      ))
  ) {
    throw new ProductionContractError();
  }
  return structuredClone({
    schema_version: 1,
    run_id: runId,
    execution_mode: "PLAN_ONLY",
    status: status as ProductionRunViewV1["status"],
    current_stage: stringValue(run.current_stage),
    created_at: createdAt,
    updated_at: updatedAt,
    normalized_input: {
      input_mode: inputMode as ProductionRunViewV1["normalized_input"]["input_mode"],
      source_content: stringValue(normalized.source_content),
      language: stringValue(normalized.language),
      visual_mode: stringValue(normalized.visual_mode),
      character_scope: stringValue(normalized.character_scope),
    },
    story_plan: storyPlan,
    timeline: timelineView,
    warnings: {
      schema_version: 1,
      warning_count: conditions.length,
      conditions,
    },
    render_readiness: {
      ready: readiness.ready,
      reason_codes: stringArray(readiness.reason_codes),
    },
  });
}

function validateCapabilities(value: unknown): ProductionCapabilitiesV1 {
  const capabilities = record(value);
  requireSchemaOne(capabilities);
  for (const field of [
    "backend_render_capability",
    "synthetic_closure_passed",
    "live_canary_passed",
    "full_render_enabled",
  ] as const) {
    if (typeof capabilities[field] !== "boolean") {
      throw new ProductionContractError();
    }
    if (capabilities[field] !== false) {
      throw new ProductionContractError();
    }
  }
  const executionModes = stringArray(capabilities.supported_execution_modes);
  const inputModes = stringArray(capabilities.supported_input_modes);
  if (
    executionModes.length !== 1 ||
    executionModes[0] !== "PLAN_ONLY" ||
    inputModes.length !== 1 ||
    inputModes[0] !== "TOPIC"
  ) {
    throw new ProductionContractError();
  }
  return structuredClone({
    schema_version: 1,
    supported_execution_modes: executionModes,
    supported_input_modes: inputModes,
    supported_languages: stringArray(capabilities.supported_languages),
    supported_aspect_ratios: stringArray(capabilities.supported_aspect_ratios),
    supported_visual_modes: stringArray(capabilities.supported_visual_modes),
    supported_character_scopes: stringArray(capabilities.supported_character_scopes),
    backend_render_capability: capabilities.backend_render_capability,
    synthetic_closure_passed: capabilities.synthetic_closure_passed,
    live_canary_passed: capabilities.live_canary_passed,
    full_render_enabled: capabilities.full_render_enabled,
    render_lock_reason_codes: stringArray(capabilities.render_lock_reason_codes),
  }) as ProductionCapabilitiesV1;
}

function validateHealth(value: unknown): PlanOnlyHealthV1 {
  const health = record(value);
  requireSchemaOne(health);
  if (
    health.status !== "ok" ||
    health.contract_version !== "v1" ||
    health.plan_only_available !== true ||
    health.render_available !== false
  ) {
    throw new ProductionContractError("The PLAN_ONLY health response is invalid.");
  }
  return structuredClone({
    schema_version: 1,
    status: "ok",
    contract_version: "v1",
    plan_only_available: true,
    render_available: false,
  });
}

function validateError(value: unknown): PublicApiErrorV1 {
  const error = record(value);
  requireSchemaOne(error);
  if (error.status !== "BLOCKED" && error.status !== "FAILED") {
    throw new ProductionContractError();
  }
  if (typeof error.retryable !== "boolean" || !Array.isArray(error.field_errors)) {
    throw new ProductionContractError();
  }
  return structuredClone({
    schema_version: 1,
    request_id: stringValue(error.request_id),
    status: error.status,
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
    details: record(error.details),
  });
}

function validateCreateResult(value: unknown): PlanOnlyCreateResultV1 {
  const result = record(value);
  requireSchemaOne(result);
  const run = result.run === null ? null : validateRun(result.run);
  const error = result.error === null ? null : validateError(result.error);
  if ((run === null) === (error === null)) {
    throw new ProductionContractError();
  }
  return { schema_version: 1, run, error };
}

function strictInteger(value: unknown): number {
  const number = finiteNumber(value);
  if (!Number.isInteger(number)) {
    throw new ProductionContractError();
  }
  return number;
}

function validateRevisionSummary(value: unknown) {
  const summary = record(value);
  const revisionNumber = strictInteger(summary.revision_number);
  const revisionId = stringValue(summary.revision_id);
  if (
    revisionId !== `revision-${revisionNumber.toString().padStart(4, "0")}` ||
    revisionNumber < 1
  ) {
    throw new ProductionContractError();
  }
  return {
    revision_id: revisionId,
    revision_number: revisionNumber,
    created_at: stringValue(summary.created_at),
    reasons: stringArray(summary.reasons),
    target_duration_seconds: positiveNumber(summary.target_duration_seconds),
    beat_count: strictInteger(summary.beat_count),
  };
}

function validateReviewStory(value: unknown) {
  const story = record(value);
  requireSchemaOne(story);
  const narrationText = stringValue(story.narration_text);
  const narrationCodePoints = Array.from(narrationText);
  const requestedSceneCount = strictInteger(story.requested_scene_count);
  if (
    (requestedSceneCount !== 7 && requestedSceneCount !== 8) ||
    !Array.isArray(story.semantic_beats) ||
    !Array.isArray(story.scenes)
  ) {
    throw new ProductionContractError();
  }
  let previousEnd = 0;
  const semanticBeats = story.semantic_beats.map((raw, index) => {
    const beat = record(raw);
    const span = record(beat.source_span);
    const start = strictInteger(span.start);
    const end = strictInteger(span.end);
    const narrationSegment = stringValue(beat.narration_segment);
    if (
      beat.order !== index + 1 ||
      start !== previousEnd ||
      end <= start ||
      narrationCodePoints.slice(start, end).join("") !== narrationSegment
    ) {
      throw new ProductionContractError();
    }
    previousEnd = end;
    return {
      beat_id: stringValue(beat.beat_id),
      order: strictInteger(beat.order),
      source_span: { start, end },
      narration_segment: narrationSegment,
      semantic_purpose: stringValue(beat.semantic_purpose),
      emotional_state: stringValue(beat.emotional_state),
      transition_intent: stringValue(beat.transition_intent),
      visual_intent: stringValue(beat.visual_intent),
      duration_seconds: positiveNumber(beat.duration_seconds),
    };
  });
  if (
    semanticBeats.length !== requestedSceneCount ||
    previousEnd !== narrationCodePoints.length ||
    new Set(semanticBeats.map((beat) => beat.beat_id)).size !== semanticBeats.length
  ) {
    throw new ProductionContractError();
  }
  const scenes = story.scenes.map((raw, index) => {
    const scene = record(raw);
    if (
      scene.order !== index + 1 ||
      scene.source_beat_id !== semanticBeats[index]?.beat_id
    ) {
      throw new ProductionContractError();
    }
    return {
      scene_id: stringValue(scene.scene_id),
      order: strictInteger(scene.order),
      source_beat_id: stringValue(scene.source_beat_id),
      meaning: stringValue(scene.meaning),
      emotional_tone: stringArray(scene.emotional_tone),
      visual_intent: stringValue(scene.visual_intent),
      planned_duration_seconds: positiveNumber(scene.planned_duration_seconds),
    };
  });
  const planner = record(story.planner_metadata);
  if (
    planner.deterministic !== true ||
    planner.external_calls !== 0 ||
    planner.production_eligible !== false ||
    planner.story_planning_authorized !== true
  ) {
    throw new ProductionContractError();
  }
  return {
    schema_version: 1 as const,
    topic: stringValue(story.topic),
    language: stringValue(story.language),
    aspect_ratio:
      story.aspect_ratio === "9:16"
        ? ("9:16" as const)
        : (() => {
            throw new ProductionContractError();
          })(),
    target_duration_seconds: positiveNumber(story.target_duration_seconds),
    requested_scene_count: requestedSceneCount as 7 | 8,
    narration_text: narrationText,
    emotional_arc: stringArray(story.emotional_arc),
    topic_intent: stringValue(story.topic_intent),
    semantic_beats: semanticBeats,
    scenes,
    planner_metadata: {
      planner_id: stringValue(planner.planner_id),
      planner_version: stringValue(planner.planner_version),
      deterministic: true as const,
      external_calls: 0 as const,
      production_eligible: false as const,
      story_planning_authorized: true as const,
    },
  };
}

function validateRevision(value: unknown): StoryPlanRevisionV1 {
  const revision = record(value);
  requireSchemaOne(revision);
  const summary = validateRevisionSummary(revision);
  const story = validateReviewStory(revision.story_plan);
  if (!Array.isArray(record(revision.timeline).rows)) {
    throw new ProductionContractError();
  }
  const timeline = record(revision.timeline);
  requireSchemaOne(timeline);
  if (
    timeline.authority !== "PLANNED" ||
    timeline.transition_profile_id !== null ||
    timeline.configured_transition_seconds !== null ||
    timeline.effective_transition_seconds !== null
  ) {
    throw new ProductionContractError();
  }
  const rows = (timeline.rows as unknown[]).map((raw, index) => {
    const row = record(raw);
    if (
      row.order !== index + 1 ||
      row.scene_id !== story.scenes[index]?.scene_id ||
      row.render_clip_duration_seconds !== null
    ) {
      throw new ProductionContractError();
    }
    return {
      scene_id: stringValue(row.scene_id),
      order: strictInteger(row.order),
      start_seconds: finiteNumber(row.start_seconds),
      narration_slot_duration_seconds: positiveNumber(
        row.narration_slot_duration_seconds,
      ),
      render_clip_duration_seconds: null,
      end_seconds: positiveNumber(row.end_seconds),
    };
  });
  const totalDuration = positiveNumber(timeline.total_duration_seconds);
  if (rows.length === 0 || rows.at(-1)?.end_seconds !== totalDuration) {
    throw new ProductionContractError();
  }
  const warnings = record(revision.warnings);
  requireSchemaOne(warnings);
  if (!Array.isArray(warnings.conditions)) {
    throw new ProductionContractError();
  }
  const conditions = warnings.conditions.map(validateCondition);
  if (warnings.warning_count !== conditions.length) {
    throw new ProductionContractError();
  }
  const duration = record(revision.duration_assessment);
  if (
    duration.policy_id !== "mvp_emotional_duration_32_38_v1" ||
    !["IN_TARGET", "OUTSIDE_TARGET_WARNING"].includes(
      stringValue(duration.status),
    ) ||
    duration.value_authority !== "PLANNED" ||
    duration.target_min_seconds !== 32 ||
    duration.target_max_seconds !== 38
  ) {
    throw new ProductionContractError();
  }
  const targetDuration = positiveNumber(duration.target_duration_seconds);
  const beatTotal = positiveNumber(duration.semantic_beat_total_seconds);
  const sceneTotal = positiveNumber(duration.scene_planning_total_seconds);
  if (
    targetDuration !== beatTotal ||
    targetDuration !== sceneTotal ||
    targetDuration !== story.target_duration_seconds ||
    targetDuration !== totalDuration
  ) {
    throw new ProductionContractError();
  }
  const identity = record(revision.identity_scope);
  const requestedScope = stringValue(identity.requested_scope);
  const anonymous = requestedScope === "female_with_anonymous_background";
  if (
    !["recurring_female", "female_with_anonymous_background"].includes(
      requestedScope,
    ) ||
    identity.supported !== true ||
    identity.recurring_female_required !== true ||
    identity.anonymous_background_people_allowed !== anonymous ||
    identity.identity_continuity_required !== true ||
    identity.visual_identity_verified !== false ||
    !Array.isArray(identity.blocking_reason_codes) ||
    identity.blocking_reason_codes.length !== 0 ||
    identity.eligibility_status !==
      (anonymous
        ? "SUPPORTED_FEMALE_WITH_ANONYMOUS_BACKGROUND"
        : "SUPPORTED_RECURRING_FEMALE")
  ) {
    throw new ProductionContractError();
  }
  if (
    summary.target_duration_seconds !== story.target_duration_seconds ||
    summary.beat_count !== story.semantic_beats.length
  ) {
    throw new ProductionContractError();
  }
  return structuredClone({
    schema_version: 1,
    run_id: stringValue(revision.run_id),
    ...summary,
    custom_note:
      revision.custom_note === null ? null : stringValue(revision.custom_note),
    story_plan: story,
    timeline: {
      schema_version: 1,
      authority: "PLANNED",
      transition_profile_id: null,
      configured_transition_seconds: null,
      effective_transition_seconds: null,
      total_duration_seconds: totalDuration,
      rows,
    },
    warnings: {
      schema_version: 1,
      warning_count: conditions.length,
      conditions,
    },
    duration_assessment: {
      policy_id: "mvp_emotional_duration_32_38_v1",
      status: duration.status as "IN_TARGET" | "OUTSIDE_TARGET_WARNING",
      reason_code: stringValue(duration.reason_code),
      value_authority: "PLANNED",
      target_duration_seconds: targetDuration,
      target_min_seconds: 32,
      target_max_seconds: 38,
      semantic_beat_total_seconds: beatTotal,
      scene_planning_total_seconds: sceneTotal,
    },
    identity_scope: {
      requested_scope: requestedScope as
        | "recurring_female"
        | "female_with_anonymous_background",
      supported: true,
      eligibility_status: identity.eligibility_status as
        | "SUPPORTED_RECURRING_FEMALE"
        | "SUPPORTED_FEMALE_WITH_ANONYMOUS_BACKGROUND",
      recurring_female_required: true,
      anonymous_background_people_allowed: anonymous,
      identity_continuity_required: true,
      visual_identity_verified: false,
      blocking_reason_codes: [],
    },
  });
}

function validateReview(value: unknown): StoryPlanReviewViewV1 {
  const review = record(value);
  requireSchemaOne(review);
  const status = stringValue(review.review_status);
  if (
    !["UNREVIEWED", "ACCEPTED_FOR_SCENE_PLANNING", "REPLAN_REQUESTED"].includes(
      status,
    ) ||
    !Array.isArray(review.revision_history) ||
    review.revision_history.length === 0 ||
    review.render_authority !== false ||
    review.media_capability !== false ||
    review.process_local !== true ||
    typeof review.scene_planning_accepted !== "boolean"
  ) {
    throw new ProductionContractError();
  }
  const history = review.revision_history.map(validateRevisionSummary);
  if (
    history.some((item, index) => item.revision_number !== index + 1) ||
    new Set(history.map((item) => item.revision_id)).size !== history.length
  ) {
    throw new ProductionContractError();
  }
  const current = validateRevision(review.current_revision);
  const currentId = stringValue(review.current_revision_id);
  const runId = stringValue(review.run_id);
  const accepted = status === "ACCEPTED_FOR_SCENE_PLANNING";
  const acceptedId =
    review.accepted_revision_id === null
      ? null
      : stringValue(review.accepted_revision_id);
  if (
    currentId !== history.at(-1)?.revision_id ||
    current.revision_id !== currentId ||
    current.run_id !== runId ||
    review.scene_planning_accepted !== accepted ||
    (accepted ? acceptedId !== currentId : acceptedId !== null)
  ) {
    throw new ProductionContractError();
  }
  return structuredClone({
    schema_version: 1,
    run_id: runId,
    review_status: status as StoryPlanReviewViewV1["review_status"],
    current_revision_id: currentId,
    accepted_revision_id: acceptedId,
    revision_history: history,
    current_revision: current,
    scene_planning_accepted: accepted,
    render_authority: false,
    media_capability: false,
    process_local: true,
  });
}

function validateRevisionHistory(value: unknown): StoryPlanRevisionHistoryV1 {
  const history = record(value);
  requireSchemaOne(history);
  if (!Array.isArray(history.revisions) || history.revisions.length === 0) {
    throw new ProductionContractError();
  }
  const revisions = history.revisions.map(validateRevisionSummary);
  const currentRevisionId = stringValue(history.current_revision_id);
  if (
    revisions.some((item, index) => item.revision_number !== index + 1) ||
    new Set(revisions.map((item) => item.revision_id)).size !== revisions.length ||
    currentRevisionId !== revisions.at(-1)?.revision_id
  ) {
    throw new ProductionContractError();
  }
  return structuredClone({
    schema_version: 1,
    run_id: stringValue(history.run_id),
    current_revision_id: currentRevisionId,
    revisions,
  });
}

function validateReviewOperation(
  value: unknown,
): StoryPlanReviewOperationResultV1 {
  const result = record(value);
  requireSchemaOne(result);
  const review = result.review === null ? null : validateReview(result.review);
  const revision =
    result.revision === null ? null : validateRevision(result.revision);
  const error = result.error === null ? null : validateError(result.error);
  if (
    (error === null && (review === null || revision === null)) ||
    (error !== null && (review !== null || revision !== null))
  ) {
    throw new ProductionContractError();
  }
  if (
    review !== null &&
    revision !== null &&
    (review.run_id !== revision.run_id ||
      review.current_revision_id !== revision.revision_id)
  ) {
    throw new ProductionContractError();
  }
  return { schema_version: 1, review, revision, error };
}

export class BackendProductionRepository implements ProductionRepository {
  readonly #transport: PlanOnlyTransport;

  constructor(transport: PlanOnlyTransport) {
    this.#transport = transport;
  }

  async createPlanOnlyRun(
    request: PlanOnlyCreateRequestV1,
  ): Promise<PlanOnlyCreateResultV1> {
    const response = await this.#transport.request(`${apiPrefix}/runs`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(request),
    });
    if (response.status !== 200) {
      throw new ProductionBackendUnavailableError();
    }
    return validateCreateResult(response.payload);
  }

  async getDashboard(): Promise<ProductionDashboardViewV1> {
    const response = await this.#transport.request(`${apiPrefix}/runs`);
    if (response.status !== 200) {
      throw new ProductionBackendUnavailableError();
    }
    const dashboard = record(response.payload);
    requireSchemaOne(dashboard);
    if (!Array.isArray(dashboard.runs)) {
      throw new ProductionContractError();
    }
    return {
      schema_version: 1,
      runs: dashboard.runs.map((raw) => {
        const summary = record(raw);
        const runStatus = stringValue(summary.status);
        if (!["PLANNING", "PLANNED", "BLOCKED", "FAILED"].includes(runStatus)) {
          throw new ProductionContractError();
        }
        return {
          run_id: stringValue(summary.run_id),
          execution_mode: "PLAN_ONLY",
          status: runStatus as ProductionRunViewV1["status"],
          current_stage: stringValue(summary.current_stage),
          warning_count: finiteNumber(summary.warning_count),
          created_at: stringValue(summary.created_at),
          updated_at: stringValue(summary.updated_at),
        };
      }),
    };
  }

  async getCapabilities(): Promise<ProductionCapabilitiesV1> {
    const response = await this.#transport.request(`${apiPrefix}/capabilities`);
    if (response.status !== 200) {
      throw new ProductionBackendUnavailableError();
    }
    return validateCapabilities(response.payload);
  }

  async getHealth(): Promise<PlanOnlyHealthV1> {
    const response = await this.#transport.request(`${apiPrefix}/health`);
    if (response.status !== 200) {
      throw new ProductionBackendUnavailableError();
    }
    return validateHealth(response.payload);
  }

  async getRun(runId: string): Promise<ProductionRunViewV1 | null> {
    const response = await this.#transport.request(
      `${apiPrefix}/runs/${encodeURIComponent(runId)}`,
    );
    if (response.status === 404) {
      return null;
    }
    if (response.status !== 200) {
      throw new ProductionBackendUnavailableError();
    }
    const run = validateRun(response.payload);
    if (run.run_id !== runId) {
      throw new ProductionContractError("Backend run identity does not match the request.");
    }
    return run;
  }

  async getStoryPlanReview(runId: string): Promise<StoryPlanReviewViewV1 | null> {
    const response = await this.#transport.request(
      `${apiPrefix}/runs/${encodeURIComponent(runId)}/review`,
    );
    if (response.status === 404) {
      return null;
    }
    if (response.status !== 200) {
      throw new ProductionBackendUnavailableError();
    }
    const review = validateReview(response.payload);
    if (review.run_id !== runId) {
      throw new ProductionContractError("Backend review identity does not match the request.");
    }
    return review;
  }

  async acceptStoryPlan(
    runId: string,
    request: AcceptStoryPlanRequestV1,
  ): Promise<StoryPlanReviewOperationResultV1> {
    const response = await this.#transport.request(
      `${apiPrefix}/runs/${encodeURIComponent(runId)}/review/accept`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(request),
      },
    );
    if (response.status !== 200) {
      throw new ProductionBackendUnavailableError();
    }
    const result = validateReviewOperation(response.payload);
    if (
      (result.review !== null && result.review.run_id !== runId) ||
      (result.revision !== null && result.revision.run_id !== runId)
    ) {
      throw new ProductionContractError("Backend review identity does not match the request.");
    }
    return result;
  }

  async requestStoryPlanReplan(
    runId: string,
    request: ReplanRequestV1,
  ): Promise<StoryPlanReviewOperationResultV1> {
    const response = await this.#transport.request(
      `${apiPrefix}/runs/${encodeURIComponent(runId)}/revisions`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(request),
      },
    );
    if (response.status !== 200) {
      throw new ProductionBackendUnavailableError();
    }
    const result = validateReviewOperation(response.payload);
    if (
      (result.review !== null && result.review.run_id !== runId) ||
      (result.revision !== null && result.revision.run_id !== runId)
    ) {
      throw new ProductionContractError("Backend revision identity does not match the request.");
    }
    return result;
  }

  async getStoryPlanRevisions(
    runId: string,
  ): Promise<StoryPlanRevisionHistoryV1 | null> {
    const response = await this.#transport.request(
      `${apiPrefix}/runs/${encodeURIComponent(runId)}/revisions`,
    );
    if (response.status === 404) {
      return null;
    }
    if (response.status !== 200) {
      throw new ProductionBackendUnavailableError();
    }
    const history = validateRevisionHistory(response.payload);
    if (history.run_id !== runId) {
      throw new ProductionContractError("Backend revision identity does not match the request.");
    }
    return history;
  }

  async getStoryPlanRevision(
    runId: string,
    revisionId: string,
  ): Promise<StoryPlanRevisionV1 | null> {
    const response = await this.#transport.request(
      `${apiPrefix}/runs/${encodeURIComponent(runId)}/revisions/${encodeURIComponent(revisionId)}`,
    );
    if (response.status === 404) {
      return null;
    }
    if (response.status !== 200) {
      throw new ProductionBackendUnavailableError();
    }
    const revision = validateRevision(response.payload);
    if (revision.run_id !== runId || revision.revision_id !== revisionId) {
      throw new ProductionContractError("Backend revision identity does not match the request.");
    }
    return revision;
  }

  async getScenePlan(runId: string): Promise<ScenePlanAccessV1 | null> {
    const response = await this.#transport.request(
      `${apiPrefix}/runs/${encodeURIComponent(runId)}/scene-plan`,
    );
    if (response.status === 404) return null;
    if (response.status !== 200) throw new ProductionBackendUnavailableError();
    const access = validateScenePlanAccess(response.payload);
    if (access.run_id !== runId) throw new ProductionContractError();
    return access;
  }

  async #sceneMutation(
    runId: string,
    path: string,
    request: object,
  ): Promise<ScenePlanOperationResultV1> {
    const response = await this.#transport.request(
      `${apiPrefix}/runs/${encodeURIComponent(runId)}/scene-plan/${path}`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(request),
      },
    );
    if (response.status !== 200) throw new ProductionBackendUnavailableError();
    const result = validateScenePlanOperation(response.payload);
    if (result.access !== null && result.access.run_id !== runId) {
      throw new ProductionContractError();
    }
    return result;
  }

  initializeScenePlan(runId: string, acceptedStoryRevisionId: string) {
    return this.#sceneMutation(runId, "initialize", {
      schema_version: 1,
      accepted_story_revision_id: acceptedStoryRevisionId,
    });
  }

  saveSceneRevision(
    runId: string,
    sceneId: string,
    request: SaveSceneRevisionRequestV1,
  ) {
    return this.#sceneMutation(
      runId,
      `scenes/${encodeURIComponent(sceneId)}/revisions`,
      request,
    );
  }

  reorderScene(runId: string, request: ReorderSceneRequestV1) {
    return this.#sceneMutation(runId, "reorder", request);
  }

  splitScene(runId: string, request: SplitSceneRequestV1) {
    return this.#sceneMutation(runId, "split", request);
  }

  mergeScenes(runId: string, request: MergeScenesRequestV1) {
    return this.#sceneMutation(runId, "merge", request);
  }

  duplicateScene(runId: string, request: DuplicateSceneRequestV1) {
    return this.#sceneMutation(runId, "duplicate", request);
  }

  acceptScene(
    runId: string,
    sceneId: string,
    request: SceneTransitionRequestV1,
  ) {
    return this.#sceneMutation(
      runId,
      `scenes/${encodeURIComponent(sceneId)}/accept`,
      request,
    );
  }

  requestSceneRevision(
    runId: string,
    sceneId: string,
    request: RequestSceneRevisionV1,
  ) {
    return this.#sceneMutation(
      runId,
      `scenes/${encodeURIComponent(sceneId)}/request-revision`,
      request,
    );
  }

  restoreSceneRevision(
    runId: string,
    sceneId: string,
    request: RestoreSceneRevisionRequestV1,
  ) {
    return this.#sceneMutation(
      runId,
      `scenes/${encodeURIComponent(sceneId)}/restore`,
      request,
    );
  }

  async getPlannedScene(runId: string, sceneId: string) {
    const response = await this.#transport.request(
      `${apiPrefix}/runs/${encodeURIComponent(runId)}/scene-plan/scenes/${encodeURIComponent(sceneId)}`,
    );
    if (response.status === 404) return null;
    if (response.status !== 200) throw new ProductionBackendUnavailableError();
    const scene = validateScenePlan(response.payload);
    if (scene.run_id !== runId || scene.scene_id !== sceneId) {
      throw new ProductionContractError();
    }
    return scene;
  }

  async getSceneHistory(runId: string, sceneId: string) {
    const response = await this.#transport.request(
      `${apiPrefix}/runs/${encodeURIComponent(runId)}/scene-plan/scenes/${encodeURIComponent(sceneId)}/history`,
    );
    if (response.status === 404) return null;
    if (response.status !== 200) throw new ProductionBackendUnavailableError();
    const history = validateSceneHistory(response.payload);
    if (history.run_id !== runId || history.scene_id !== sceneId) {
      throw new ProductionContractError();
    }
    return history;
  }

  async getScenePlanRevisions(runId: string) {
    const response = await this.#transport.request(
      `${apiPrefix}/runs/${encodeURIComponent(runId)}/scene-plan/revisions`,
    );
    if (response.status === 404) return null;
    if (response.status !== 200) throw new ProductionBackendUnavailableError();
    const history = validateScenePlanCollectionHistory(response.payload);
    if (history.run_id !== runId) throw new ProductionContractError();
    return history;
  }

  async getScenePlanCollectionRevision(runId: string, revisionId: string) {
    const response = await this.#transport.request(
      `${apiPrefix}/runs/${encodeURIComponent(runId)}/scene-plan/revisions/${encodeURIComponent(revisionId)}`,
    );
    if (response.status === 404) return null;
    if (response.status !== 200) throw new ProductionBackendUnavailableError();
    const collection = validateScenePlanCollection(response.payload);
    if (
      collection.run_id !== runId ||
      collection.collection_revision_id !== revisionId
    ) {
      throw new ProductionContractError();
    }
    return collection;
  }

  async getVisualCandidateAccess(runId: string, sceneId: string) {
    const response = await this.#transport.request(
      `${apiPrefix}/runs/${encodeURIComponent(runId)}/scene-plan/scenes/${encodeURIComponent(sceneId)}/visual-candidates`,
    );
    if (response.status === 404) return null;
    if (response.status !== 200) throw new ProductionBackendUnavailableError();
    const access = validateVisualCandidateAccess(response.payload);
    if (access.run_id !== runId || access.scene_id !== sceneId) {
      throw new ProductionContractError();
    }
    return access;
  }

  async #visualMutation(
    runId: string,
    sceneId: string,
    path: string,
    request: object,
  ) {
    const response = await this.#transport.request(
      `${apiPrefix}/runs/${encodeURIComponent(runId)}/scene-plan/scenes/${encodeURIComponent(sceneId)}/visual-candidates/${path}`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(request),
      },
    );
    if (response.status !== 200) throw new ProductionBackendUnavailableError();
    const result = validateVisualCandidateOperation(response.payload);
    if (
      result.access !== null &&
      (result.access.run_id !== runId || result.access.scene_id !== sceneId)
    ) {
      throw new ProductionContractError();
    }
    return result;
  }

  generateVisualCandidates(
    runId: string,
    sceneId: string,
    request: GenerateVisualCandidatesRequestV1,
  ) {
    return this.#visualMutation(runId, sceneId, "generate", request);
  }

  async getVisualCandidate(
    runId: string,
    sceneId: string,
    candidateId: string,
  ) {
    const response = await this.#transport.request(
      `${apiPrefix}/runs/${encodeURIComponent(runId)}/scene-plan/scenes/${encodeURIComponent(sceneId)}/visual-candidates/${encodeURIComponent(candidateId)}`,
    );
    if (response.status === 404) return null;
    if (response.status !== 200) throw new ProductionBackendUnavailableError();
    const candidate = validateVisualCandidate(response.payload);
    if (
      candidate.run_id !== runId ||
      candidate.scene_id !== sceneId ||
      candidate.candidate_id !== candidateId
    ) {
      throw new ProductionContractError();
    }
    return candidate;
  }

  mutateVisualCandidate(
    runId: string,
    sceneId: string,
    candidateId: string,
    operation: "accept" | "reject" | "request-revision",
    request: VisualCandidateMutationRequestV1,
  ) {
    return this.#visualMutation(
      runId,
      sceneId,
      `${encodeURIComponent(candidateId)}/${operation}`,
      request,
    );
  }

  async getCompositionAccess(runId: string) {
    const response = await this.#transport.request(
      `${apiPrefix}/runs/${encodeURIComponent(runId)}/compositions/access`,
    );
    if (response.status === 404) return null;
    if (response.status !== 200) throw new ProductionBackendUnavailableError();
    const access = validateCompositionAccess(response.payload);
    if (access.run_id !== runId) throw new ProductionContractError();
    return access;
  }

  async #compositionMutation(
    runId: string,
    path: string,
    request: object,
  ): Promise<CompositionOperationResultV1> {
    const response = await this.#transport.request(
      `${apiPrefix}/runs/${encodeURIComponent(runId)}/compositions/${path}`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(request),
      },
    );
    if (response.status !== 200) throw new ProductionBackendUnavailableError();
    const result = validateCompositionOperation(response.payload);
    if (
      (result.access !== null && result.access?.run_id !== runId) ||
      (result.collection !== null && result.collection?.run_id !== runId)
    ) {
      throw new ProductionContractError();
    }
    return result;
  }

  initializeCompositions(runId: string, scenePlanCollectionRevisionId: string) {
    return this.#compositionMutation(runId, "initialize", {
      schema_version: 1,
      scene_plan_collection_revision_id: scenePlanCollectionRevisionId,
    });
  }

  saveComposition(
    runId: string,
    sceneId: string,
    request: Parameters<NonNullable<ProductionRepository["saveComposition"]>>[2],
  ) {
    return this.#compositionMutation(
      runId,
      `scenes/${encodeURIComponent(sceneId)}/revisions`,
      request,
    );
  }

  reviewComposition(
    runId: string,
    sceneId: string,
    operation: "accept" | "request-revision",
    request: Parameters<NonNullable<ProductionRepository["reviewComposition"]>>[3],
  ) {
    return this.#compositionMutation(
      runId,
      `scenes/${encodeURIComponent(sceneId)}/${operation}`,
      request,
    );
  }

  restoreComposition(
    runId: string,
    sceneId: string,
    request: Parameters<NonNullable<ProductionRepository["restoreComposition"]>>[2],
  ) {
    return this.#compositionMutation(
      runId,
      `scenes/${encodeURIComponent(sceneId)}/restore`,
      request,
    );
  }

  async getCompositionHistory(runId: string, sceneId: string) {
    const response = await this.#transport.request(
      `${apiPrefix}/runs/${encodeURIComponent(runId)}/compositions/scenes/${encodeURIComponent(sceneId)}/history`,
    );
    if (response.status === 404) return null;
    if (response.status !== 200) throw new ProductionBackendUnavailableError();
    const payload = response.payload as Record<string, unknown>;
    if (
      payload.schema_version !== 1 ||
      payload.run_id !== runId ||
      payload.scene_id !== sceneId ||
      !Array.isArray(payload.revisions)
    ) {
      throw new ProductionContractError();
    }
    return {
      schema_version: 1 as const,
      run_id: runId,
      scene_id: sceneId,
      revisions: payload.revisions.map(validateSceneComposition),
    };
  }

  async getTimelineAccess(runId: string) {
    const response = await this.#transport.request(
      `${apiPrefix}/runs/${encodeURIComponent(runId)}/timeline/access`,
    );
    if (response.status === 404) return null;
    if (response.status !== 200) throw new ProductionBackendUnavailableError();
    const access = validateTimelineAccess(response.payload);
    if (access.run_id !== runId) throw new ProductionContractError();
    return access;
  }

  async #timelineMutation(
    runId: string,
    path: string,
    request: object,
  ): Promise<TimelineOperationResultV1> {
    const response = await this.#transport.request(
      `${apiPrefix}/runs/${encodeURIComponent(runId)}/timeline/${path}`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(request),
      },
    );
    if (response.status !== 200) throw new ProductionBackendUnavailableError();
    const result = validateTimelineOperation(response.payload);
    if (
      (result.access !== null && result.access.run_id !== runId) ||
      (result.collection !== null && result.collection.run_id !== runId)
    ) {
      throw new ProductionContractError();
    }
    return result;
  }

  initializeTimeline(runId: string, compositionCollectionRevisionId: string) {
    return this.#timelineMutation(runId, "initialize", {
      schema_version: 1,
      composition_collection_revision_id: compositionCollectionRevisionId,
    });
  }

  saveTimelineSegment(
    runId: string,
    segmentId: string,
    request: Parameters<
      NonNullable<ProductionRepository["saveTimelineSegment"]>
    >[2],
  ) {
    return this.#timelineMutation(
      runId,
      `segments/${encodeURIComponent(segmentId)}/revisions`,
      request,
    );
  }

  restoreTimelineSegment(
    runId: string,
    segmentId: string,
    request: Parameters<
      NonNullable<ProductionRepository["restoreTimelineSegment"]>
    >[2],
  ) {
    return this.#timelineMutation(
      runId,
      `segments/${encodeURIComponent(segmentId)}/restore`,
      request,
    );
  }

  reviewTimeline(
    runId: string,
    operation: "accept" | "request-revision",
    request: Parameters<NonNullable<ProductionRepository["reviewTimeline"]>>[2],
  ) {
    return this.#timelineMutation(runId, operation, request);
  }

  async getTimelineSegmentHistory(runId: string, segmentId: string) {
    const response = await this.#transport.request(
      `${apiPrefix}/runs/${encodeURIComponent(runId)}/timeline/segments/${encodeURIComponent(segmentId)}/history`,
    );
    if (response.status === 404) return null;
    if (response.status !== 200) throw new ProductionBackendUnavailableError();
    const payload = response.payload as Record<string, unknown>;
    if (
      payload.schema_version !== 1 ||
      payload.run_id !== runId ||
      payload.segment_id !== segmentId ||
      !Array.isArray(payload.revisions)
    ) {
      throw new ProductionContractError();
    }
    return {
      schema_version: 1 as const,
      run_id: runId,
      segment_id: segmentId,
      revisions: payload.revisions.map(validateTimelineSegment),
    };
  }
}

export const backendProductionRepository = new BackendProductionRepository(
  defineNativeFetchClient(globalThis.fetch.bind(globalThis)),
);
