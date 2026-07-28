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

export interface PlanOnlyHealthV1 {
  readonly schema_version: 1;
  readonly status: "ok";
  readonly contract_version: "v1";
  readonly plan_only_available: true;
  readonly render_available: false;
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

export type StoryPlanReviewStatusV1 =
  | "UNREVIEWED"
  | "ACCEPTED_FOR_SCENE_PLANNING"
  | "REPLAN_REQUESTED";

export type ReplanFeedbackDimensionV1 =
  | "narration_too_short"
  | "narration_too_long"
  | "story_focus_incorrect"
  | "emotional_progression_weak"
  | "character_scope_incorrect"
  | "scene_count_unsuitable"
  | "custom_note";

export interface ReviewSemanticBeatV1 extends SemanticBeatViewV1 {
  readonly source_span: {
    readonly start: number;
    readonly end: number;
  };
  readonly transition_intent: string;
}

export interface PlannerMetadataViewV1 {
  readonly planner_id: string;
  readonly planner_version: string;
  readonly deterministic: true;
  readonly external_calls: 0;
  readonly production_eligible: false;
  readonly story_planning_authorized: true;
}

export interface DurationAssessmentViewV1 {
  readonly policy_id: "mvp_emotional_duration_32_38_v1";
  readonly status: "IN_TARGET" | "OUTSIDE_TARGET_WARNING";
  readonly reason_code: string;
  readonly value_authority: "PLANNED";
  readonly target_duration_seconds: number;
  readonly target_min_seconds: 32;
  readonly target_max_seconds: 38;
  readonly semantic_beat_total_seconds: number;
  readonly scene_planning_total_seconds: number;
}

export interface IdentityScopeViewV1 {
  readonly requested_scope:
    | "recurring_female"
    | "female_with_anonymous_background";
  readonly supported: true;
  readonly eligibility_status:
    | "SUPPORTED_RECURRING_FEMALE"
    | "SUPPORTED_FEMALE_WITH_ANONYMOUS_BACKGROUND";
  readonly recurring_female_required: true;
  readonly anonymous_background_people_allowed: boolean;
  readonly identity_continuity_required: true;
  readonly visual_identity_verified: false;
  readonly blocking_reason_codes: readonly string[];
}

export interface ReviewStoryPlanViewV1 {
  readonly schema_version: 1;
  readonly topic: string;
  readonly language: string;
  readonly aspect_ratio: "9:16";
  readonly target_duration_seconds: number;
  readonly requested_scene_count: 7 | 8;
  readonly narration_text: string;
  readonly emotional_arc: readonly string[];
  readonly topic_intent: string;
  readonly semantic_beats: readonly ReviewSemanticBeatV1[];
  readonly scenes: readonly SceneViewV1[];
  readonly planner_metadata: PlannerMetadataViewV1;
}

export interface StoryPlanRevisionV1 {
  readonly schema_version: 1;
  readonly run_id: string;
  readonly revision_id: string;
  readonly revision_number: number;
  readonly created_at: string;
  readonly reasons: readonly string[];
  readonly custom_note: string | null;
  readonly target_duration_seconds: number;
  readonly beat_count: number;
  readonly story_plan: ReviewStoryPlanViewV1;
  readonly timeline: TimelineViewV1;
  readonly warnings: WarningCollectionV1;
  readonly duration_assessment: DurationAssessmentViewV1;
  readonly identity_scope: IdentityScopeViewV1;
}

export interface StoryPlanRevisionSummaryV1 {
  readonly revision_id: string;
  readonly revision_number: number;
  readonly created_at: string;
  readonly reasons: readonly string[];
  readonly target_duration_seconds: number;
  readonly beat_count: number;
}

export interface StoryPlanReviewViewV1 {
  readonly schema_version: 1;
  readonly run_id: string;
  readonly review_status: StoryPlanReviewStatusV1;
  readonly current_revision_id: string;
  readonly accepted_revision_id: string | null;
  readonly revision_history: readonly StoryPlanRevisionSummaryV1[];
  readonly current_revision: StoryPlanRevisionV1;
  readonly scene_planning_accepted: boolean;
  readonly render_authority: false;
  readonly media_capability: false;
  readonly process_local: true;
}

export interface StoryPlanRevisionHistoryV1 {
  readonly schema_version: 1;
  readonly run_id: string;
  readonly current_revision_id: string;
  readonly revisions: readonly StoryPlanRevisionSummaryV1[];
}

export interface AcceptStoryPlanRequestV1 {
  readonly schema_version: 1;
  readonly current_revision_id: string;
}

export interface ReplanRequestV1 {
  readonly schema_version: 1;
  readonly base_revision_id: string;
  readonly feedback: readonly ReplanFeedbackDimensionV1[];
  readonly custom_note: string | null;
}

export interface StoryPlanReviewOperationResultV1 {
  readonly schema_version: 1;
  readonly review: StoryPlanReviewViewV1 | null;
  readonly revision: StoryPlanRevisionV1 | null;
  readonly error: PublicApiErrorV1 | null;
}
