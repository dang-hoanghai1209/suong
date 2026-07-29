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

export type ScenePlanStatusV1 =
  | "DRAFT"
  | "VALID"
  | "WARNING"
  | "BLOCKED"
  | "ACCEPTED_FOR_VISUAL_PLANNING"
  | "REVISION_REQUESTED";

export interface ScenePlanV1 {
  readonly schema_version: 1;
  readonly run_id: string;
  readonly source_story_revision_id: string;
  readonly scene_id: string;
  readonly scene_revision_id: string;
  readonly scene_revision_number: number;
  readonly order: number;
  readonly source_beat_id: string;
  readonly source_coverage: {
    readonly start: number;
    readonly end: number;
    readonly overlap_draft: boolean;
  };
  readonly narration_segment: string;
  readonly objective: string;
  readonly emotional_intent: string;
  readonly environment: string;
  readonly character_action: string;
  readonly objects: readonly string[];
  readonly composition_guidance: string;
  readonly continuity_notes: string;
  readonly camera_motion_intent: string;
  readonly transition_intent: string;
  readonly planned_duration_seconds: number;
  readonly planning_note: string;
  readonly status: ScenePlanStatusV1;
  readonly warning_codes: readonly string[];
  readonly blocker_codes: readonly string[];
  readonly continuity: {
    readonly recurring_character_required: boolean;
    readonly anonymous_background_allowed: boolean;
    readonly identity_continuity_required: true;
    readonly environment_continuity: "PLANNING_REQUIRED";
    readonly object_continuity: "PLANNING_REQUIRED";
    readonly visual_identity_verified: false;
  };
  readonly created_at: string;
}

export interface ScenePlanRevisionSummaryV1 {
  readonly collection_revision_id: string;
  readonly revision_number: number;
  readonly created_at: string;
  readonly reason: string;
  readonly affected_scene_ids: readonly string[];
}

export interface ScenePlanCollectionV1 {
  readonly schema_version: 1;
  readonly run_id: string;
  readonly source_story_revision_id: string;
  readonly accepted_story_revision_id: string;
  readonly collection_revision_id: string;
  readonly collection_revision_number: number;
  readonly created_at: string;
  readonly reason: string;
  readonly source_narration_length: number;
  readonly scenes: readonly ScenePlanV1[];
  readonly revision_history: readonly ScenePlanRevisionSummaryV1[];
  readonly target_duration_seconds: number;
  readonly target_min_seconds: 32;
  readonly target_max_seconds: 38;
  readonly total_planned_duration_seconds: number;
  readonly duration_valid: boolean;
  readonly source_coverage_valid: boolean;
  readonly ready_for_visual_planning: boolean;
  readonly render_authority: false;
  readonly media_capability: false;
  readonly process_local: true;
}

export interface ScenePlanAccessV1 {
  readonly schema_version: 1;
  readonly run_id: string;
  readonly editable: boolean;
  readonly blocker_codes: readonly string[];
  readonly collection: ScenePlanCollectionV1 | null;
  readonly render_authority: false;
  readonly media_capability: false;
  readonly process_local: true;
}

export interface ScenePlanOperationResultV1 {
  readonly schema_version: 1;
  readonly access: ScenePlanAccessV1 | null;
  readonly error: PublicApiErrorV1 | null;
}

export interface ScenePlanEditChangesV1 {
  readonly objective?: string;
  readonly emotional_intent?: string;
  readonly environment?: string;
  readonly character_action?: string;
  readonly objects?: readonly string[];
  readonly composition_guidance?: string;
  readonly continuity_notes?: string;
  readonly camera_motion_intent?: string;
  readonly transition_intent?: string;
  readonly planned_duration_seconds?: number;
  readonly planning_note?: string;
}

export interface SaveSceneRevisionRequestV1 {
  readonly schema_version: 1;
  readonly base_collection_revision_id: string;
  readonly base_scene_revision_id: string;
  readonly changes: ScenePlanEditChangesV1;
}

export interface ScenePlanMutationBaseV1 {
  readonly schema_version: 1;
  readonly base_collection_revision_id: string;
}

export interface SceneTransitionRequestV1 extends ScenePlanMutationBaseV1 {
  readonly scene_id: string;
  readonly base_scene_revision_id: string;
}

export interface RequestSceneRevisionV1 extends SceneTransitionRequestV1 {
  readonly reason_code:
    | "OBJECTIVE_NEEDS_REVISION"
    | "CONTINUITY_NEEDS_REVISION"
    | "DURATION_NEEDS_REVISION"
    | "SOURCE_LINKAGE_NEEDS_REVISION"
    | "OTHER_PLANNING_REVISION";
  readonly note: string | null;
}

export interface ReorderSceneRequestV1 extends ScenePlanMutationBaseV1 {
  readonly scene_id: string;
  readonly direction: "UP" | "DOWN";
}

export interface SplitSceneRequestV1 extends ScenePlanMutationBaseV1 {
  readonly scene_id: string;
  readonly base_scene_revision_id: string;
  readonly split_at: number;
  readonly first_duration_seconds: number;
  readonly second_duration_seconds: number;
}

export interface MergeScenesRequestV1 extends ScenePlanMutationBaseV1 {
  readonly first_scene_id: string;
  readonly second_scene_id: string;
}

export interface DuplicateSceneRequestV1 extends ScenePlanMutationBaseV1 {
  readonly scene_id: string;
}

export interface RestoreSceneRevisionRequestV1 extends ScenePlanMutationBaseV1 {
  readonly base_scene_revision_id: string;
  readonly restore_scene_revision_id: string;
  readonly reason: string;
}

export interface ScenePlanSceneHistoryV1 {
  readonly schema_version: 1;
  readonly run_id: string;
  readonly scene_id: string;
  readonly current_scene_revision_id: string;
  readonly revisions: readonly ScenePlanV1[];
}

export interface ScenePlanCollectionHistoryV1 {
  readonly schema_version: 1;
  readonly run_id: string;
  readonly current_collection_revision_id: string;
  readonly revisions: readonly ScenePlanRevisionSummaryV1[];
}

export type VisualCandidateStatusV1 =
  | "REVIEW_PENDING"
  | "ACCEPTED_FOR_COMPOSITION_PLANNING"
  | "REJECTED"
  | "REVISION_REQUESTED"
  | "INVALID"
  | "SUPERSEDED";

export type VisualRejectionReasonV1 =
  | "CHARACTER_IDENTITY_MISMATCH"
  | "POSE_MISMATCH"
  | "ACTION_MISMATCH"
  | "ENVIRONMENT_MISMATCH"
  | "OBJECT_MISMATCH"
  | "COMPOSITION_MISMATCH"
  | "STYLE_MISMATCH"
  | "CONTINUITY_MISMATCH"
  | "TEXT_OR_WATERMARK_PRESENT"
  | "INVALID_ANATOMY"
  | "LOW_IMAGE_QUALITY"
  | "DUPLICATE_CANDIDATE"
  | "OTHER_BOUNDED_NOTE";

export interface VisualReviewEntryV1 {
  readonly review_id: string;
  readonly review_number: number;
  readonly candidate_id: string;
  readonly candidate_revision_id: string;
  readonly action: "ACCEPTED" | "REJECTED" | "REVISION_REQUESTED" | "SUPERSEDED";
  readonly reason_code: string;
  readonly note: string | null;
  readonly created_at: string;
}

export interface VisualCandidateV1 {
  readonly schema_version: 1;
  readonly candidate_id: string;
  readonly candidate_revision_id: string;
  readonly candidate_revision_number: number;
  readonly request_id: string;
  readonly attempt_id: string;
  readonly run_id: string;
  readonly story_revision_id: string;
  readonly scene_plan_collection_revision_id: string;
  readonly scene_id: string;
  readonly scene_revision_id: string;
  readonly semantic_beat_id: string;
  readonly source_coverage: { readonly start: number; readonly end: number };
  readonly status: VisualCandidateStatusV1;
  readonly artifact_url: string;
  readonly provider_capability_label: string;
  readonly model_label: string;
  readonly mime_type: "image/png" | "image/jpeg" | "image/webp";
  readonly extension: ".png" | ".jpg" | ".webp";
  readonly width: number;
  readonly height: number;
  readonly sha256: string;
  readonly logical_request_hash: string;
  readonly provider_request_hash: string;
  readonly technical_validation: {
    readonly passed: boolean;
    readonly mime_valid: boolean;
    readonly dimensions_valid: boolean;
    readonly non_empty: boolean;
    readonly animation_free: boolean;
    readonly duplicate_free: boolean;
    readonly blocker_codes: readonly string[];
  };
  readonly visual_qc: {
    readonly blocker_codes: readonly string[];
    readonly warning_codes: readonly string[];
    readonly information_codes: readonly string[];
  };
  readonly review_history: readonly VisualReviewEntryV1[];
  readonly accepted_for_composition_planning: boolean;
  readonly superseded_reason: string | null;
  readonly created_at: string;
}

export interface VisualGenerationRequestV1 {
  readonly request_id: string;
  readonly request_number: number;
  readonly visual_collection_revision_id: string;
  readonly candidate_count: number;
  readonly aspect_ratio: "9:16";
  readonly composition_emphasis: string | null;
  readonly correction_dimensions: readonly VisualRejectionReasonV1[];
  readonly note: string | null;
  readonly prompt_projection: string;
  readonly logical_request_hash: string;
  readonly attempt_ids: readonly string[];
  readonly status: "SUCCESS" | "PARTIAL_SUCCESS" | "FAILED";
  readonly created_at: string;
}

export interface VisualGenerationAttemptV1 {
  readonly attempt_id: string;
  readonly attempt_number: number;
  readonly request_id: string;
  readonly candidate_ids: readonly string[];
  readonly requested_candidate_count: number;
  readonly returned_candidate_count: number;
  readonly invalid_candidate_count: number;
  readonly provider_capability_label: string;
  readonly model_label: string;
  readonly status: "SUCCESS" | "PARTIAL_SUCCESS" | "FAILED";
  readonly reason_code: string;
  readonly created_at: string;
}

export interface VisualCandidateCollectionV1 {
  readonly schema_version: 1;
  readonly run_id: string;
  readonly story_revision_id: string;
  readonly scene_plan_collection_revision_id: string;
  readonly scene_id: string;
  readonly scene_revision_id: string;
  readonly semantic_beat_id: string;
  readonly source_coverage: { readonly start: number; readonly end: number };
  readonly visual_authority_version: "visual_candidate_authority_v1";
  readonly visual_collection_id: string;
  readonly visual_collection_revision_id: string;
  readonly visual_collection_revision_number: number;
  readonly current_request_id: string | null;
  readonly current_accepted_candidate_id: string | null;
  readonly requests: readonly VisualGenerationRequestV1[];
  readonly attempts: readonly VisualGenerationAttemptV1[];
  readonly candidates: readonly VisualCandidateV1[];
  readonly render_authority: false;
  readonly video_render_authority: false;
  readonly final_media_capability: false;
  readonly narration_generation_capability: false;
  readonly tts_capability: false;
  readonly process_local: true;
  readonly created_at: string;
}

export interface VisualCandidateAccessV1 {
  readonly schema_version: 1;
  readonly run_id: string;
  readonly scene_id: string;
  readonly request_authorized: boolean;
  readonly review_authorized: boolean;
  readonly blocker_codes: readonly string[];
  readonly current_story_revision_id: string | null;
  readonly accepted_story_revision_id: string | null;
  readonly current_scene_plan_collection_revision_id: string | null;
  readonly current_scene_revision_id: string | null;
  readonly current_visual_collection_revision_id: string | null;
  readonly current_request_id: string | null;
  readonly current_accepted_candidate_id: string | null;
  readonly provider_capability: {
    readonly available: boolean;
    readonly capability_label: string;
    readonly supports_9_16: true;
    readonly maximum_candidate_count: 4;
    readonly external_provider: true;
    readonly reason_code: string | null;
  };
  readonly collection: VisualCandidateCollectionV1 | null;
  readonly render_authority: false;
  readonly video_render_authority: false;
  readonly final_media_capability: false;
  readonly narration_generation_capability: false;
  readonly tts_capability: false;
  readonly process_local: true;
}

export interface GenerateVisualCandidatesRequestV1 {
  readonly schema_version: 1;
  readonly visual_collection_revision_id: string;
  readonly scene_plan_collection_revision_id: string;
  readonly scene_revision_id: string;
  readonly candidate_count: number;
  readonly aspect_ratio: "9:16";
  readonly composition_emphasis: string | null;
  readonly correction_dimensions: readonly VisualRejectionReasonV1[];
  readonly note: string | null;
}

export interface VisualCandidateMutationRequestV1 {
  readonly schema_version: 1;
  readonly visual_collection_revision_id: string;
  readonly scene_plan_collection_revision_id: string;
  readonly scene_revision_id: string;
  readonly candidate_revision_id: string;
  readonly reason_code: VisualRejectionReasonV1;
  readonly note: string | null;
}

export interface VisualCandidateOperationResultV1 {
  readonly schema_version: 1;
  readonly access: VisualCandidateAccessV1 | null;
  readonly error: {
    readonly schema_version: 1;
    readonly code: string;
    readonly message: string;
    readonly retryable: boolean;
  } | null;
}

export type CompositionStatusV1 =
  | "DRAFT"
  | "VALID"
  | "WARNING"
  | "BLOCKED"
  | "ACCEPTED_FOR_TIMELINE_PLANNING"
  | "REVISION_REQUESTED"
  | "SUPERSEDED";
export type CompositionFitModeV1 =
  | "CONTAIN"
  | "COVER"
  | "FIT_WIDTH"
  | "FIT_HEIGHT"
  | "MANUAL_CROP";
export type CompositionMotionModeV1 =
  | "STATIC"
  | "SLOW_ZOOM_IN"
  | "SLOW_ZOOM_OUT"
  | "PAN_LEFT"
  | "PAN_RIGHT"
  | "PAN_UP"
  | "PAN_DOWN"
  | "CUSTOM_START_END";
export type CompositionTransitionV1 =
  | "CUT"
  | "CROSSFADE"
  | "FADE_THROUGH_COLOR"
  | "DIP_TO_BLACK"
  | "HOLD_THEN_CUT";
export type CompositionReviewReasonV1 =
  | "FRAMING_UNSUITABLE"
  | "CROP_UNSUITABLE"
  | "SUBJECT_POSITION_UNSUITABLE"
  | "CHARACTER_SCALE_INCONSISTENT"
  | "SAFE_ZONE_CONFLICT"
  | "LAYER_ORDER_INCORRECT"
  | "MOTION_INTENT_UNSUITABLE"
  | "TRANSITION_INTENT_UNSUITABLE"
  | "CONTINUITY_MISMATCH"
  | "COLOR_TREATMENT_UNSUITABLE"
  | "OTHER_BOUNDED_NOTE";

export interface NormalizedGeometryV1 {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
  readonly anchor_x: number;
  readonly anchor_y: number;
  readonly scale: number;
  readonly rotation_degrees: number;
  readonly opacity: number;
}
export interface NormalizedCropV1 {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}
export interface CompositionMotionIntentV1 {
  readonly mode: CompositionMotionModeV1;
  readonly start_scale: number;
  readonly end_scale: number;
  readonly start_anchor_x: number;
  readonly start_anchor_y: number;
  readonly end_anchor_x: number;
  readonly end_anchor_y: number;
}
export interface SceneCompositionV1 {
  readonly schema_version: 1;
  readonly composition_id: string;
  readonly composition_revision_id: string;
  readonly composition_revision_number: number;
  readonly position: number;
  readonly run_id: string;
  readonly story_revision_id: string;
  readonly scene_plan_collection_revision_id: string;
  readonly scene_id: string;
  readonly scene_revision_id: string;
  readonly visual_collection_revision_id: string;
  readonly accepted_candidate_id: string;
  readonly accepted_candidate_revision_id: string;
  readonly accepted_candidate_sha256: string;
  readonly artifact_url: string;
  readonly source_width: number;
  readonly source_height: number;
  readonly source_mime: "image/png" | "image/jpeg" | "image/webp";
  readonly source_coverage: { readonly start: number; readonly end: number };
  readonly semantic_beat_id: string;
  readonly planned_duration_seconds: number;
  readonly aspect_ratio: "9:16";
  readonly composition_contract_version: "composition_planning_v1";
  readonly status: CompositionStatusV1;
  readonly fit_mode: CompositionFitModeV1;
  readonly crop: NormalizedCropV1;
  readonly placement: NormalizedGeometryV1;
  readonly safe_margins: {
    readonly top: 0.08;
    readonly bottom: 0.12;
    readonly left: 0.06;
    readonly right: 0.06;
    readonly title_safe: 0.1;
    readonly subtitle_safe: 0.16;
  };
  readonly layers: readonly {
    readonly layer_id: string;
    readonly layer_type:
      | "ACCEPTED_VISUAL"
      | "SAFE_COLOR_WASH"
      | "SAFE_GRADIENT_OVERLAY";
    readonly z_order: number;
    readonly enabled: boolean;
    readonly geometry: NormalizedGeometryV1;
    readonly opacity: number;
    readonly color: string | null;
  }[];
  readonly motion_intent: CompositionMotionIntentV1;
  readonly transition_intent: CompositionTransitionV1;
  readonly transition_duration_seconds: number;
  readonly note: string | null;
  readonly validation: {
    readonly valid: boolean;
    readonly blocker_codes: readonly string[];
    readonly warning_codes: readonly string[];
    readonly information_codes: readonly string[];
  };
  readonly continuity_codes: readonly string[];
  readonly changed_fields: readonly string[];
  readonly review_history: readonly {
    readonly review_id: string;
    readonly action: "ACCEPTED" | "REVISION_REQUESTED" | "SUPERSEDED";
    readonly reason_code: string;
    readonly note: string | null;
    readonly composition_revision_id: string;
    readonly created_at: string;
  }[];
  readonly accepted_for_timeline_planning: boolean;
  readonly superseded_reason: string | null;
  readonly created_at: string;
  readonly render_authority: false;
  readonly renderer_execution_authority: false;
  readonly video_render_authority: false;
  readonly timeline_execution_authority: false;
  readonly final_media_capability: false;
  readonly narration_generation_capability: false;
  readonly tts_capability: false;
}
export interface CompositionCollectionV1 {
  readonly schema_version: 1;
  readonly collection_id: string;
  readonly collection_revision_id: string;
  readonly collection_revision_number: number;
  readonly run_id: string;
  readonly story_revision_id: string;
  readonly scene_plan_collection_revision_id: string;
  readonly compositions: readonly SceneCompositionV1[];
  readonly missing_scene_ids: readonly string[];
  readonly total_scene_count: number;
  readonly valid_composition_count: number;
  readonly warning_composition_count: number;
  readonly blocked_composition_count: number;
  readonly accepted_for_timeline_planning_count: number;
  readonly overall_ready_for_timeline_planning: boolean;
  readonly created_at: string;
  readonly render_authority: false;
  readonly renderer_execution_authority: false;
  readonly video_render_authority: false;
  readonly timeline_execution_authority: false;
  readonly final_media_capability: false;
}
export interface CompositionAccessV1 {
  readonly schema_version: 1;
  readonly run_id: string;
  readonly editable: boolean;
  readonly initialization_authorized: boolean;
  readonly blocker_codes: readonly string[];
  readonly current_story_revision_id: string | null;
  readonly current_scene_plan_collection_revision_id: string | null;
  readonly collection: CompositionCollectionV1 | null;
  readonly process_local: true;
  readonly render_authority: false;
  readonly renderer_execution_authority: false;
  readonly video_render_authority: false;
  readonly timeline_execution_authority: false;
  readonly final_media_capability: false;
  readonly narration_generation_capability: false;
  readonly tts_capability: false;
}
export interface CompositionEditV1 {
  readonly fit_mode?: CompositionFitModeV1;
  readonly crop?: NormalizedCropV1;
  readonly placement?: NormalizedGeometryV1;
  readonly motion_intent?: CompositionMotionIntentV1;
  readonly transition_intent?: CompositionTransitionV1;
  readonly transition_duration_seconds?: number;
  readonly overlay_color?: string | null;
  readonly note?: string | null;
}
export interface CompositionOperationResultV1 {
  readonly schema_version: 1;
  readonly access?: CompositionAccessV1 | null;
  readonly collection?: CompositionCollectionV1 | null;
  readonly error: {
    readonly schema_version: 1;
    readonly code: string;
    readonly message: string;
    readonly retryable: false;
  } | null;
}

export type TimelineStatusV1 =
  | "VALID"
  | "WARNING"
  | "BLOCKED"
  | "ACCEPTED_FOR_EXECUTION_REVIEW"
  | "REVISION_REQUESTED"
  | "SUPERSEDED";
export type NarrationAlignmentStatusV1 =
  | "ALIGNED"
  | "WARNING"
  | "BLOCKED"
  | "REVISION_REQUESTED"
  | "SUPERSEDED";
export type TimelineReviewReasonV1 =
  | "DURATION_OUT_OF_RANGE"
  | "NARRATION_WINDOW_UNSUITABLE"
  | "TRANSITION_TIMING_UNSUITABLE"
  | "SOURCE_ALIGNMENT_MISMATCH"
  | "CONTINUITY_MISMATCH"
  | "OTHER_BOUNDED_NOTE";

export interface TimelineSegmentV1 {
  readonly schema_version: 1;
  readonly segment_id: string;
  readonly segment_revision_id: string;
  readonly segment_revision_number: number;
  readonly position: number;
  readonly run_id: string;
  readonly story_revision_id: string;
  readonly narration_source_sha256: string;
  readonly scene_plan_collection_revision_id: string;
  readonly scene_id: string;
  readonly scene_revision_id: string;
  readonly visual_collection_revision_id: string;
  readonly accepted_candidate_id: string;
  readonly accepted_candidate_revision_id: string;
  readonly candidate_artifact_sha256: string;
  readonly composition_collection_revision_id: string;
  readonly composition_id: string;
  readonly composition_revision_id: string;
  readonly semantic_beat_id: string;
  readonly source_coverage: { readonly start: number; readonly end: number };
  readonly canonical_narration_segment: string;
  readonly scene_planned_duration_ms: number;
  readonly duration_ms: number;
  readonly start_ms: number;
  readonly end_ms: number;
  readonly transition_in_intent: string;
  readonly transition_in_ms: number;
  readonly transition_out_intent: string;
  readonly transition_out_ms: number;
  readonly effective_visible_duration_ms: number;
  readonly narration_alignment: {
    readonly alignment_id: string;
    readonly canonical_narration_segment: string;
    readonly source_coverage: { readonly start: number; readonly end: number };
    readonly semantic_beat_id: string;
    readonly timeline_start_ms: number;
    readonly timeline_end_ms: number;
    readonly planned_window_start_ms: number;
    readonly planned_window_end_ms: number;
    readonly status: NarrationAlignmentStatusV1;
    readonly source_coverage_valid: boolean;
    readonly warning_codes: readonly string[];
    readonly blocker_codes: readonly string[];
    readonly measured_audio_alignment_available: false;
    readonly tts_alignment_available: false;
  };
  readonly motion_intent_summary: string;
  readonly continuity_codes: readonly string[];
  readonly warning_codes: readonly string[];
  readonly blocker_codes: readonly string[];
  readonly status: TimelineStatusV1;
  readonly note: string | null;
  readonly changed_fields: readonly string[];
  readonly superseded_reason: string | null;
  readonly created_at: string;
  readonly timeline_contract_version: "timeline_planning_v1";
  readonly accepted_for_execution_review: false;
  readonly timeline_execution_authority: false;
  readonly narration_generation_capability: false;
  readonly tts_capability: false;
  readonly audio_generation_capability: false;
  readonly renderer_execution_authority: false;
  readonly render_authority: false;
  readonly video_render_authority: false;
  readonly final_media_capability: false;
}

export interface TimelineCollectionV1 {
  readonly schema_version: 1;
  readonly collection_id: string;
  readonly collection_revision_id: string;
  readonly collection_revision_number: number;
  readonly run_id: string;
  readonly story_revision_id: string;
  readonly narration_source_sha256: string;
  readonly source_authority_sha256: string;
  readonly scene_plan_collection_revision_id: string;
  readonly composition_collection_revision_id: string;
  readonly segments: readonly TimelineSegmentV1[];
  readonly total_segment_count: number;
  readonly total_planned_duration_ms: number;
  readonly total_transition_overlap_ms: number;
  readonly effective_timeline_duration_ms: number;
  readonly target_min_ms: number;
  readonly target_max_ms: number;
  readonly narration_coverage_valid: boolean;
  readonly ordering_valid: boolean;
  readonly duration_valid: boolean;
  readonly transition_valid: boolean;
  readonly warning_segment_count: number;
  readonly blocked_segment_count: number;
  readonly overall_ready_for_execution_review: boolean;
  readonly status: TimelineStatusV1;
  readonly accepted_for_execution_review: boolean;
  readonly accepted_timeline_revision_id: string | null;
  readonly review_history: readonly {
    readonly review_id: string;
    readonly action: "ACCEPTED" | "REVISION_REQUESTED" | "SUPERSEDED";
    readonly reason_code: string;
    readonly note: string | null;
    readonly collection_revision_id: string;
    readonly created_at: string;
  }[];
  readonly created_at: string;
  readonly process_local: true;
  readonly full_render_enabled: false;
  readonly timeline_execution_authority: false;
  readonly narration_generation_capability: false;
  readonly tts_capability: false;
  readonly audio_generation_capability: false;
  readonly renderer_execution_authority: false;
  readonly render_authority: false;
  readonly video_render_authority: false;
  readonly final_media_capability: false;
}

export interface TimelineAccessV1 {
  readonly schema_version: 1;
  readonly run_id: string;
  readonly editable: boolean;
  readonly initialization_authorized: boolean;
  readonly blocker_codes: readonly string[];
  readonly current_story_revision_id: string | null;
  readonly current_scene_plan_collection_revision_id: string | null;
  readonly current_composition_collection_revision_id: string | null;
  readonly collection: TimelineCollectionV1 | null;
  readonly process_local: true;
  readonly full_render_enabled: false;
  readonly timeline_execution_authority: false;
  readonly narration_generation_capability: false;
  readonly tts_capability: false;
  readonly audio_generation_capability: false;
  readonly renderer_execution_authority: false;
  readonly render_authority: false;
  readonly video_render_authority: false;
  readonly final_media_capability: false;
}

export interface TimelineOperationResultV1 {
  readonly schema_version: 1;
  readonly access: TimelineAccessV1 | null;
  readonly collection: TimelineCollectionV1 | null;
  readonly error: {
    readonly schema_version: 1;
    readonly code: string;
    readonly message: string;
    readonly retryable: false;
  } | null;
}

export type ReadinessStatusV1 =
  | "LOCKED"
  | "NOT_READY"
  | "READY_FOR_EXECUTION_ENABLEMENT_REVIEW"
  | "REVIEW_APPROVED_FOR_SEPARATE_TASK"
  | "REVISION_REQUESTED"
  | "REVIEW_REJECTED"
  | "SUPERSEDED";
export type ReadinessReviewReasonV1 =
  | "STORY_PLAN_REVIEW_REQUIRED"
  | "SCENE_PLANNING_REVIEW_REQUIRED"
  | "VISUAL_REVIEW_REQUIRED"
  | "COMPOSITION_REVIEW_REQUIRED"
  | "TIMELINE_REVIEW_REQUIRED"
  | "NARRATION_ALIGNMENT_REVIEW_REQUIRED"
  | "OTHER_BOUNDED_NOTE";

export interface ReadinessStageCheckV1 {
  readonly schema_version: 1;
  readonly check_id: string;
  readonly stage: string;
  readonly status: "PASS" | "WARNING" | "BLOCKED" | "NOT_EVALUATED" | "SUPERSEDED";
  readonly blocker_codes: readonly string[];
  readonly warning_codes: readonly string[];
  readonly informational_codes: readonly string[];
  readonly bound_revision_ids: readonly string[];
  readonly summary: string;
  readonly required_for_review_eligibility: boolean;
  readonly checked_at: string;
}

export interface ReadinessSceneCheckV1 {
  readonly schema_version: 1;
  readonly scene_check_id: string;
  readonly position: number;
  readonly scene_id: string;
  readonly scene_revision_id: string;
  readonly semantic_beat_id: string;
  readonly source_coverage: {
    readonly schema_version: 1;
    readonly start: number;
    readonly end: number;
  };
  readonly visual_collection_revision_id: string;
  readonly accepted_candidate_id: string;
  readonly candidate_revision_id: string;
  readonly artifact_sha256: string;
  readonly artifact_mime: string;
  readonly artifact_width: number;
  readonly artifact_height: number;
  readonly artifact_registered: true;
  readonly artifact_technically_valid: true;
  readonly composition_id: string;
  readonly composition_revision_id: string;
  readonly composition_accepted: true;
  readonly timeline_segment_id: string;
  readonly timeline_segment_revision_id: string;
  readonly start_ms: number;
  readonly end_ms: number;
  readonly duration_ms: number;
  readonly transition_duration_ms: number;
  readonly narration_alignment_status: "ALIGNED";
  readonly blocker_codes: readonly string[];
  readonly warning_codes: readonly string[];
  readonly readiness_status: "PASS";
}

export interface ReadinessLimitationV1 {
  readonly schema_version: 1;
  readonly limitation_code: string;
  readonly description: string;
  readonly acknowledgement_required: true;
  readonly acknowledged: boolean;
  readonly acknowledgement_id: string | null;
}

export interface ReadinessAcknowledgementV1 {
  readonly schema_version: 1;
  readonly acknowledgement_id: string;
  readonly report_revision_id: string;
  readonly source_authority_sha256: string;
  readonly limitation_code: string;
  readonly acknowledged: true;
  readonly reviewer_note: string | null;
  readonly created_at: string;
}

export interface ReadinessApprovalV1 extends ReadinessCapabilityLocksV1 {
  readonly schema_version: 1;
  readonly approval_id: string;
  readonly report_revision_id: string;
  readonly source_authority_sha256: string;
  readonly purpose: "SEPARATE_EXECUTION_ENABLEMENT_TASK_REVIEW";
  readonly approved_for_separate_execution_enablement_review: true;
  readonly reviewer_note: string | null;
  readonly created_at: string;
}

export interface ReadinessAuthorityBindingsV1 {
  readonly schema_version: 1;
  readonly story_plan_revision_id: string;
  readonly accepted_story_plan_revision_id: string;
  readonly narration_source_sha256: string;
  readonly scene_collection_revision_id: string;
  readonly visual_collection_revision_ids: readonly string[];
  readonly accepted_candidate_ids: readonly string[];
  readonly accepted_candidate_revision_ids: readonly string[];
  readonly accepted_candidate_artifact_sha256s: readonly string[];
  readonly composition_collection_revision_id: string;
  readonly composition_ids: readonly string[];
  readonly composition_revision_ids: readonly string[];
  readonly timeline_collection_revision_id: string;
  readonly accepted_timeline_revision_id: string;
  readonly timeline_source_authority_sha256: string;
}

export interface ReadinessCapabilityLocksV1 {
  readonly full_render_enabled: false;
  readonly render_authority: false;
  readonly renderer_execution_authority: false;
  readonly video_render_authority: false;
  readonly timeline_execution_authority: false;
  readonly narration_generation_capability: false;
  readonly tts_capability: false;
  readonly audio_generation_capability: false;
  readonly subtitle_generation_capability: false;
  readonly media_muxing_capability: false;
  readonly final_media_capability: false;
  readonly output_creation_capability: false;
  readonly execution_job_creation_capability: false;
}

export interface ExecutionReadinessReportV1 extends ReadinessCapabilityLocksV1 {
  readonly schema_version: 1;
  readonly report_id: string;
  readonly report_revision_id: string;
  readonly report_revision_number: number;
  readonly run_id: string;
  readonly source_authority_sha256: string;
  readonly status: ReadinessStatusV1;
  readonly current: boolean;
  readonly planning_package_ready: boolean;
  readonly future_execution_enablement_review_eligible: boolean;
  readonly approved_for_separate_execution_enablement_review: boolean;
  readonly blocker_count: number;
  readonly warning_count: number;
  readonly informational_count: number;
  readonly stage_checks: readonly ReadinessStageCheckV1[];
  readonly scene_checks: readonly ReadinessSceneCheckV1[];
  readonly limitations: readonly ReadinessLimitationV1[];
  readonly acknowledgements: readonly ReadinessAcknowledgementV1[];
  readonly approval: ReadinessApprovalV1 | null;
  readonly review_history: readonly {
    readonly schema_version: 1;
    readonly review_id: string;
    readonly report_revision_id: string;
    readonly source_authority_sha256: string;
    readonly action: string;
    readonly reason_code: string | null;
    readonly note: string | null;
    readonly created_at: string;
  }[];
  readonly authority: ReadinessAuthorityBindingsV1;
  readonly total_effective_timeline_duration_ms: number;
  readonly narration_coverage_valid: true;
  readonly transition_valid: true;
  readonly target_duration_valid: true;
  readonly process_local: true;
  readonly created_at: string;
}

export interface ExecutionReadinessAccessV1 extends ReadinessCapabilityLocksV1 {
  readonly schema_version: 1;
  readonly run_id: string;
  readonly review_available: boolean;
  readonly initialization_authorized: boolean;
  readonly mutation_authorized: boolean;
  readonly blocker_codes: readonly string[];
  readonly report: ExecutionReadinessReportV1 | null;
  readonly process_local: true;
}

export interface ExecutionReadinessOperationResultV1 {
  readonly schema_version: 1;
  readonly access: ExecutionReadinessAccessV1 | null;
  readonly report: ExecutionReadinessReportV1 | null;
  readonly error: {
    readonly schema_version: 1;
    readonly code: string;
    readonly message: string;
    readonly retryable: false;
  } | null;
}
