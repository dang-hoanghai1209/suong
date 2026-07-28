import type {
  PlanOnlyCreateRequestV1,
  PlanOnlyCreateResultV1,
  PlanOnlyHealthV1,
  ProductionCapabilitiesV1,
  ProductionDashboardViewV1,
  ProductionRunViewV1,
  PublicApiErrorV1,
} from "../contracts/v1/production";

const apiPrefix = "/api/v1/plan-only";

export interface ProductionRepository {
  createPlanOnlyRun(request: PlanOnlyCreateRequestV1): Promise<PlanOnlyCreateResultV1>;
  getDashboard(): Promise<ProductionDashboardViewV1>;
  getCapabilities(): Promise<ProductionCapabilitiesV1>;
  getHealth(): Promise<PlanOnlyHealthV1>;
  getRun(runId: string): Promise<ProductionRunViewV1 | null>;
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
      const exactCreate = method === "POST" && exactCollection;
      const exactList = method === "GET" && exactCollection;
      if (
        path.includes("://") ||
        !(exactCapabilities || exactHealth || exactLookup || exactCreate || exactList)
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
}

export const backendProductionRepository = new BackendProductionRepository(
  defineNativeFetchClient(globalThis.fetch.bind(globalThis)),
);
