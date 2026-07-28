export type ExecutionModeV1 = "MOCK" | "PLAN_ONLY" | "FULL_RENDER";

export type ProductionSummaryStatusV1 =
  | "PLANNING"
  | "PLANNED"
  | "BLOCKED"
  | "FAILED"
  | "COMPLETED_WITH_WARNINGS"
  | "COMPLETED";

export type InputModeV1 = "TOPIC" | "NARRATION" | "NARRATION_WITH_SCENE_HINTS";
export type TimelineAuthorityV1 = "PLANNED" | "MEASURED";

export interface PublicConditionV1 {
  readonly code: string;
  readonly title: string;
  readonly detail: string;
  readonly severity: "INFO" | "WARNING" | "BLOCKED" | "ERROR";
  readonly scene_ids: readonly string[];
  readonly beat_ids: readonly string[];
}

export interface ProductionCapabilitiesV1 {
  readonly schema_version: 1;
  readonly supported_execution_modes: readonly ExecutionModeV1[];
  readonly supported_input_modes: readonly InputModeV1[];
  readonly supported_languages: readonly string[];
  readonly supported_aspect_ratios: readonly string[];
  readonly supported_visual_modes: readonly string[];
  readonly supported_character_scopes: readonly string[];
  readonly backend_render_capability: boolean;
  readonly synthetic_closure_passed: boolean;
  readonly live_canary_passed: boolean;
  readonly full_render_enabled: boolean;
  readonly render_lock_reason_codes: readonly string[];
}

export interface SemanticBeatViewV1 {
  readonly beat_id: string;
  readonly order: number;
  readonly narration_segment: string;
  readonly semantic_purpose: string;
  readonly emotional_state: string;
  readonly visual_intent: string;
  readonly duration_seconds: number;
}

export interface SceneViewV1 {
  readonly scene_id: string;
  readonly order: number;
  readonly source_beat_id: string;
  readonly meaning: string;
  readonly emotional_tone: readonly string[];
  readonly visual_intent: string;
  readonly planned_duration_seconds: number;
}

export interface StoryPlanViewV1 {
  readonly schema_version: 1;
  readonly topic: string;
  readonly language: string;
  readonly aspect_ratio: "9:16";
  readonly target_duration_seconds: number;
  readonly narration_text: string;
  readonly emotional_arc: readonly string[];
  readonly semantic_beats: readonly SemanticBeatViewV1[];
  readonly scenes: readonly SceneViewV1[];
}

export interface TimelineRowV1 {
  readonly scene_id: string;
  readonly order: number;
  readonly start_seconds: number;
  readonly narration_slot_duration_seconds: number;
  readonly render_clip_duration_seconds: number | null;
  readonly end_seconds: number;
}

export interface TimelineViewV1 {
  readonly schema_version: 1;
  readonly authority: TimelineAuthorityV1;
  readonly transition_profile_id: string | null;
  readonly configured_transition_seconds: number | null;
  readonly effective_transition_seconds: number | null;
  readonly total_duration_seconds: number;
  readonly rows: readonly TimelineRowV1[];
}

export interface WarningCollectionV1 {
  readonly schema_version: 1;
  readonly warning_count: number;
  readonly conditions: readonly PublicConditionV1[];
}

export interface RenderReadinessV1 {
  readonly ready: boolean;
  readonly reason_codes: readonly string[];
}

export interface ProductionRunViewV1 {
  readonly schema_version: 1;
  readonly run_id: string;
  readonly execution_mode: ExecutionModeV1;
  readonly status: ProductionSummaryStatusV1;
  readonly current_stage: string;
  readonly created_at: string;
  readonly updated_at: string;
  readonly normalized_input: {
    readonly input_mode: InputModeV1;
    readonly source_content: string;
    readonly language: string;
    readonly visual_mode: string;
    readonly character_scope: string;
  };
  readonly story_plan: StoryPlanViewV1 | null;
  readonly timeline: TimelineViewV1 | null;
  readonly warnings: WarningCollectionV1;
  readonly render_readiness: RenderReadinessV1;
}

export interface ProductionRunSummaryV1 {
  readonly run_id: string;
  readonly execution_mode: ExecutionModeV1;
  readonly status: ProductionSummaryStatusV1;
  readonly current_stage: string;
  readonly warning_count: number;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface ProductionDashboardViewV1 {
  readonly schema_version: 1;
  readonly runs: readonly ProductionRunSummaryV1[];
}

export interface PublicApiErrorV1 {
  readonly schema_version: 1;
  readonly request_id: string;
  readonly status: "BLOCKED" | "FAILED";
  readonly code: string;
  readonly message: string;
  readonly retryable: boolean;
  readonly field_errors: readonly {
    readonly path: string;
    readonly code: string;
    readonly message: string;
  }[];
  readonly details: Readonly<Record<string, unknown>>;
}

export interface PlanOnlyCreateRequestV1 {
  readonly schema_version: 1;
  readonly input_mode: InputModeV1;
  readonly source_content: string;
  readonly language: "en" | "vi";
  readonly character_scope:
    | "recurring_female"
    | "female_with_anonymous_background"
    | "recurring_male"
    | "family"
    | "unresolved";
  readonly requested_scene_count: 7 | 8;
}

export interface PlanOnlyCreateResultV1 {
  readonly schema_version: 1;
  readonly run: ProductionRunViewV1 | null;
  readonly error: PublicApiErrorV1 | null;
}
