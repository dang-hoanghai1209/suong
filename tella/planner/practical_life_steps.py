"""Deterministic planner for the practical_life_steps_v1 recipe."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from itertools import combinations
from typing import Any

from tella._voice_pace import VoicePace, normalize_voice_rate
from tella.planner.models import Scene, TellaScenePlan
from tella.planner.practical_life_steps_visuals import apply_practical_life_steps_visuals
from tella.planner.voices import edge_voice_for


_ROLES_7 = (
    "hook",
    "context",
    "practical_step",
    "practical_step",
    "practical_step",
    "common_mistake",
    "today_action",
)
_ROLES_8_CONTEXT = (
    "hook",
    "context_part_one",
    "context_part_two",
    "practical_step",
    "practical_step",
    "practical_step",
    "common_mistake",
    "today_action",
)
_ROLES_8_CORRECTION = (
    "hook",
    "context",
    "practical_step",
    "practical_step",
    "practical_step",
    "common_mistake",
    "correction",
    "today_action",
)
_ROLES_8_CLOSING = (
    "hook",
    "context",
    "practical_step",
    "practical_step",
    "practical_step",
    "common_mistake",
    "today_action",
    "closing",
)

_DEFAULT_RECIPE_PACE = VoicePace(
    name="custom",
    edge_rate="-2%",
    google_rate=0.98,
)
_DURATION_TARGET_SECONDS = 35.0
_PREFERRED_DURATION_RANGE = (34.0, 36.0)
_HARD_DURATION_RANGE = (32.0, 38.0)
_MINIMUM_SPECIFICITY_SCORE = 0.75
_DUPLICATE_STEP_THRESHOLD = 0.68
_MINIMUM_ACTION_DENSITY = 0.50
_MAXIMUM_REFLECTION_RATIO = 0.42
_MAXIMUM_LIFE_INSIGHT_OVERLAP = 0.42
_MAXIMUM_ABSTRACT_MOTIVATION_RATIO = 0.28


@dataclass(frozen=True)
class ActionDefinition:
    canonical: str
    aliases: tuple[str, ...]
    scope: str


_ACTIONS = (
    ActionDefinition("viết", ("viết", "ghi ra", "ghi lại"), "externalize_information"),
    ActionDefinition("ghi nhận", ("ghi nhận", "theo dõi"), "observe_pattern"),
    ActionDefinition("tắt", ("tắt", "im lặng"), "notification_control"),
    ActionDefinition("đặt", ("đặt",), "environment_control"),
    ActionDefinition("di chuyển", ("di chuyển", "dời"), "environment_control"),
    ActionDefinition("chuẩn bị", ("chuẩn bị",), "preparation"),
    ActionDefinition("chọn", ("chọn",), "focused_execution"),
    ActionDefinition("thực hiện", ("thực hiện", "làm"), "focused_execution"),
    ActionDefinition("dành", ("dành",), "time_blocking"),
    ActionDefinition("đặt giới hạn", ("đặt giới hạn", "đặt ranh giới"), "boundary_setting"),
    ActionDefinition("nói", ("nói",), "boundary_setting"),
    ActionDefinition("xóa", ("xóa", "bỏ"), "remove_obstacle"),
    ActionDefinition("quan sát", ("quan sát",), "observe_pattern"),
)

_VAGUE_ONLY_TERMS = (
    "yêu bản thân hơn",
    "suy nghĩ tích cực",
    "chỉ cần kỷ luật",
    "ngừng quan tâm",
    "mạnh mẽ hơn",
    "tin vào quá trình",
    "tập trung vào bản thân",
    "buông bỏ",
    "cải thiện tư duy",
    "cố gắng tốt hơn",
)
_PURCHASE_TERMS = ("mua ", "đặt mua", "trả tiền", "thanh toán")
_PAID_SERVICE_TERMS = (
    "ứng dụng trả phí",
    "gói trả phí",
    "đăng ký trả phí",
    "khóa học trả phí",
)
_HIGH_STAKES_TERMS = (
    "tự sát",
    "tự hại",
    "ngừng thuốc",
    "bỏ thuốc",
    "chẩn đoán",
    "điều trị",
    "kiện tụng",
    "kết luận pháp lý",
    "đầu tư",
    "cổ phiếu",
    "tiền mã hóa",
    "vay nợ",
)
_UNSAFE_TERMS = (
    "trả đũa",
    "đe dọa",
    "đối đầu bằng bạo lực",
    "theo dõi bí mật",
    "đọc trộm",
    "lừa dối",
    "thao túng",
)
_UNSUPPORTED_CLAIM_TERMS = (
    "chắc chắn chữa khỏi",
    "đảm bảo thành công",
    "luôn luôn có nghĩa",
    "chắc chắn họ nghĩ",
)
_EMOTIONAL_TERMS = (
    "buồn",
    "cô đơn",
    "tổn thương",
    "trống rỗng",
    "đau lòng",
    "mệt mỏi",
)
_HARSH_TRUTH_TERMS = (
    "sự thật khó chịu",
    "dấu hiệu cho thấy",
    "thực chất là",
    "nếu tiếp tục",
    "không hề coi trọng",
)
_DANGLING_WORDS = {"và", "nhưng", "vì", "khi", "nếu", "để", "nên"}
_PREDICATE_TERMS = {
    "là",
    "có",
    "xảy ra",
    "xuất hiện",
    "khiến",
    "giúp",
    "cần",
    "mất",
    "nằm",
    "bắt đầu",
    "thay đổi",
    "bỏ cuộc",
    *(alias for action in _ACTIONS for alias in action.aliases),
}
_CONDITION_MARKERS = (
    "trước khi",
    "trước phiên",
    "sau khi",
    "trong ",
    "mỗi khi",
    "khi ",
    "vào lúc",
    "cho đến khi",
)
_VISUAL_ACTION_PHRASES = {
    "viết": "writing one objective on",
    "ghi nhận": "recording one observation on",
    "tắt": "silencing",
    "đặt": "placing",
    "di chuyển": "moving",
    "chuẩn bị": "arranging",
    "chọn": "selecting",
    "thực hiện": "working with",
    "dành": "setting aside",
    "đặt giới hạn": "setting a boundary with",
    "nói": "stating one boundary beside",
    "xóa": "removing",
    "quan sát": "observing",
}
_VISUAL_PROVIDER_UNSAFE_PATTERNS = (
    r"\breadable (?:text|writing|numbers?)\b",
    r"\b(?:step numbers?|labels?|logos?|watermarks?)\b",
    r"\bui text\b",
    r"\bspeech bubbles? containing text\b",
)


def plan_practical_life_steps_from_script(
    *,
    user_script: str,
    target_lang: str,
    aspect_ratio: str = "9:16",
    media_source: str = "ai_image",
    duration_mode: str = "short",
    voice_pace: VoicePace | None = None,
    voice_gender: str | None = None,
    seed: int = 0,
) -> TellaScenePlan:
    del seed
    if target_lang != "vi":
        raise ValueError("practical_life_steps_v1 currently requires Vietnamese narration")
    segments = _script_segments(user_script)
    if len(segments) not in {7, 8}:
        raise ValueError(
            "practical_life_steps requires exactly 7 or 8 narration paragraphs; "
            f"received {len(segments)}"
        )
    roles = _roles_for_segments(segments)
    pace = voice_pace or _DEFAULT_RECIPE_PACE
    gender = (voice_gender or "female").lower()
    scenes = [
        Scene(
            scene_index=index,
            kind="scene",
            title=_role_title(role, index, roles),
            voice_script=text,
            image_prompt="planner-only wordless practical composition",
            stock_query="practical everyday action",
            scene_meaning=text,
            visual_mode="practical_planner_only",
        )
        for index, (role, text) in enumerate(zip(roles, segments), start=1)
    ]
    plan = TellaScenePlan(
        title=segments[0][:120],
        language="vi",
        aspect_ratio=aspect_ratio,
        media_source=media_source,
        duration_mode=duration_mode,
        theme="practical_life_steps",
        voice_pace_name=pace.name,
        voice_edge_rate=pace.edge_rate,
        voice_google_rate=pace.google_rate,
        voice_gender=gender,
        voice_name=edge_voice_for("vi", gender),
        subtitle_style="practical_steps_reel",
        tts_continuous=True,
        tts_text_source="global_narration_text",
        global_narration_text=" ".join(segments),
        scenes=scenes,
    )
    enforce_practical_life_steps_plan(plan, roles=roles)
    return apply_practical_life_steps_visuals(plan)


def plan_practical_life_steps_from_topic(*, topic: str, target_lang: str, **kwargs: Any):
    del topic, target_lang, kwargs
    raise ValueError(
        "practical_life_steps_v1 requires --script-file or --exact-script; "
        "topic-only advice generation is intentionally unavailable"
    )


def enforce_practical_life_steps_plan(
    plan: TellaScenePlan,
    *,
    roles: tuple[str, ...] | None = None,
) -> None:
    scenes = [scene for scene in plan.scenes if scene.kind == "scene"]
    resolved_roles = roles or _roles_for_segments([scene.voice_script for scene in scenes])
    errors: list[str] = []
    if len(scenes) not in {7, 8}:
        errors.append(f"practical plan must contain 7 or 8 scenes, got {len(scenes)}")
    if len(resolved_roles) != len(scenes):
        errors.append("scene-role assignment does not match scene count")

    _initialize_scene_metadata(scenes, resolved_roles, plan.voice_edge_rate)
    _fit_duration(plan, scenes, resolved_roles)
    if plan.duration_validation_status == "failed":
        errors.append(plan.duration_failure_reason)

    _initialize_scene_metadata(scenes, resolved_roles, plan.voice_edge_rate)
    step_scenes = [scene for scene in scenes if scene.scene_role == "practical_step"]
    if len(step_scenes) != 3:
        errors.append(f"exactly three practical steps are required, got {len(step_scenes)}")
    for expected, scene in enumerate(step_scenes, start=1):
        scene.step_number = expected
        _stamp_actionability(scene)
        if scene.actionability_status != "passed":
            errors.append(
                f"practical_step_{expected} is not actionable: "
                + ", ".join(scene.actionability_failure_reasons)
            )

    _stamp_duplicate_diagnostics(plan, step_scenes)
    if plan.duplicate_step_validation_status != "passed":
        errors.append(
            "practical steps are not materially distinct: "
            + ", ".join(plan.duplicate_step_pairs)
        )

    _stamp_safety_diagnostics(plan, scenes)
    if plan.safety_status != "passed":
        errors.extend(plan.safety_failure_reasons)

    _stamp_naturalness(scenes)
    naturalness_failures = [
        f"scene {scene.scene_index}: {reason}"
        for scene in scenes
        for reason in scene.naturalness_failure_reasons
    ]
    errors.extend(naturalness_failures)

    _stamp_visual_metadata(scenes)
    visual_failures = [
        f"practical_step_{scene.step_number} visual metadata: {reason}"
        for scene in step_scenes
        for reason in scene.visual_metadata_failure_reasons
    ]
    errors.extend(visual_failures)
    _stamp_overlap_diagnostics(plan, scenes)
    if plan.overlap_validation_status != "passed":
        errors.extend(plan.overlap_failure_reasons)

    plan.global_narration_text = " ".join(scene.voice_script for scene in scenes)
    plan.practical_validation_errors = list(dict.fromkeys(error for error in errors if error))
    plan.practical_validation_status = (
        "passed" if not plan.practical_validation_errors else "failed"
    )
    if plan.practical_validation_errors:
        raise ValueError(
            "practical life steps plan validation failed: "
            + "; ".join(plan.practical_validation_errors)
        )


def _initialize_scene_metadata(
    scenes: list[Scene],
    roles: tuple[str, ...],
    voice_rate: str,
) -> None:
    step_number = 0
    for index, (scene, role) in enumerate(zip(scenes, roles)):
        if not scene.original_voice_script:
            scene.original_voice_script = scene.voice_script
        scene.fitted_voice_script = scene.voice_script
        scene.scene_role = role
        if role == "practical_step":
            step_number += 1
            scene.step_number = step_number
        else:
            scene.step_number = 0
        words = _word_count(scene.voice_script)
        scene.narration_word_count = words
        scene.fitted_narration_word_count = words
        scene.estimated_duration_seconds = _estimate_scene_duration(
            words,
            index,
            voice_rate,
        )
        scene.fitted_estimated_duration_seconds = scene.estimated_duration_seconds
        original_words = _word_count(scene.original_voice_script)
        scene.original_narration_word_count = original_words
        scene.original_estimated_duration_seconds = _estimate_scene_duration(
            original_words,
            index,
            voice_rate,
        )


def _fit_duration(
    plan: TellaScenePlan,
    scenes: list[Scene],
    roles: tuple[str, ...],
) -> None:
    original_words = sum(_word_count(scene.original_voice_script) for scene in scenes)
    original_duration = _estimated_total(
        [scene.original_voice_script for scene in scenes],
        plan.voice_edge_rate,
    )
    plan.original_total_word_count = original_words
    plan.original_estimated_duration_seconds = original_duration
    plan.duration_target_seconds = _DURATION_TARGET_SECONDS
    plan.narration_fit_required = not (
        _HARD_DURATION_RANGE[0] <= original_duration <= _HARD_DURATION_RANGE[1]
    )
    plan.narration_fit_applied = False
    plan.narration_fit_pass_count = 0

    if original_duration > _HARD_DURATION_RANGE[1]:
        role_priority = (
            "context",
            "context_part_one",
            "context_part_two",
            "hook",
            "common_mistake",
            "correction",
            "closing",
            "today_action",
            "practical_step",
        )
        for role in role_priority:
            for scene in scenes:
                if scene.scene_role != role:
                    continue
                candidate, operations = _semantic_fit_candidate(
                    scene.voice_script,
                    role,
                )
                if candidate == scene.voice_script:
                    continue
                if role == "practical_step" and not _action_metadata(candidate).is_actionable:
                    continue
                candidate_total = _estimated_total(
                    [candidate if item is scene else item.voice_script for item in scenes],
                    plan.voice_edge_rate,
                )
                if candidate_total < _HARD_DURATION_RANGE[0]:
                    continue
                scene.voice_script = candidate
                scene.fitted_voice_script = candidate
                scene.narration_rewritten = True
                scene.rewrite_operations.extend(
                    operation
                    for operation in operations
                    if operation not in scene.rewrite_operations
                )
                plan.narration_fit_applied = True
                if candidate_total <= _PREFERRED_DURATION_RANGE[1]:
                    break
            if _estimated_total(
                [scene.voice_script for scene in scenes],
                plan.voice_edge_rate,
            ) <= _PREFERRED_DURATION_RANGE[1]:
                break
        if plan.narration_fit_applied:
            plan.narration_fit_pass_count = 1

    fitted_words = sum(_word_count(scene.voice_script) for scene in scenes)
    fitted_duration = _estimated_total(
        [scene.voice_script for scene in scenes],
        plan.voice_edge_rate,
    )
    plan.fitted_total_word_count = fitted_words
    plan.fitted_estimated_duration_seconds = fitted_duration
    plan.duration_reduction_seconds = round(original_duration - fitted_duration, 2)
    plan.duration_reduction_ratio = round(
        (original_duration - fitted_duration) / max(original_duration, 0.01),
        3,
    )
    if fitted_duration < _HARD_DURATION_RANGE[0]:
        plan.duration_validation_status = "failed"
        plan.narration_fit_status = "failed"
        plan.duration_failure_reason = (
            f"fitted duration {fitted_duration:.2f}s is below 32s; "
            "the planner will not invent additional advice"
        )
    elif fitted_duration > _HARD_DURATION_RANGE[1]:
        plan.duration_validation_status = "failed"
        plan.narration_fit_status = "failed"
        plan.duration_failure_reason = (
            f"fitted duration {fitted_duration:.2f}s exceeds 38s after safe "
            "sentence-level compression"
        )
    else:
        plan.duration_validation_status = "passed"
        plan.duration_failure_reason = ""
        plan.narration_fit_status = (
            "passed" if plan.narration_fit_applied else "not_required"
        )


@dataclass(frozen=True)
class ActionMetadata:
    action_verb: str = ""
    required_subject: str = ""
    required_object: str = ""
    action_condition: str = ""
    action_scope: str = ""
    estimated_effort: str = ""
    immediate_action_possible: bool = False
    requires_purchase: bool = False
    requires_paid_service: bool = False
    specificity_score: float = 0.0
    failure_reasons: tuple[str, ...] = ()

    @property
    def is_actionable(self) -> bool:
        return not self.failure_reasons and self.specificity_score >= _MINIMUM_SPECIFICITY_SCORE


def _action_metadata(text: str) -> ActionMetadata:
    source = re.sub(r"\s+", " ", (text or "").strip())
    source_lower = source.casefold()
    selected: tuple[ActionDefinition, re.Match[str]] | None = None
    for action in _ACTIONS:
        for alias in sorted(action.aliases, key=len, reverse=True):
            match = re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", source_lower)
            if match and (selected is None or match.start() < selected[1].start()):
                selected = (action, match)
    action_verb = selected[0].canonical if selected else ""
    action_scope = selected[0].scope if selected else ""
    subject = ""
    required_object = ""
    condition = ""
    if selected:
        subject = "người xem"
        remainder = source[selected[1].end():].strip(" ,:;-.")
        remainder = re.split(r"(?<=[.!?…])\s+", remainder, maxsplit=1)[0].strip()
        marker_positions = [
            (remainder.casefold().find(marker), marker)
            for marker in _CONDITION_MARKERS
            if remainder.casefold().find(marker) >= 0
        ]
        if marker_positions:
            marker_index, _ = min(marker_positions, key=lambda item: item[0])
            required_object = remainder[:marker_index].strip(" ,:;-.")
            condition = remainder[marker_index:].strip(" ,:;-.")
        else:
            required_object = remainder.strip(" ,:;-.")
    key = _ascii_key(source)
    purchase = any(_ascii_key(term) in key for term in _PURCHASE_TERMS)
    paid = any(_ascii_key(term) in key for term in _PAID_SERVICE_TERMS)
    delayed = any(term in key for term in ("mot ngay nao do", "khi co dieu kien"))
    immediate = bool(action_verb and required_object and not delayed)
    estimated_effort = (
        "short_defined_interval"
        if re.search(r"\b(phút|giờ|lần)\b", source_lower)
        else "small_immediate_action"
    )
    score = round(
        0.20 * bool(action_verb)
        + 0.15 * bool(subject)
        + 0.25 * bool(required_object)
        + 0.15 * bool(condition)
        + 0.05 * bool(action_scope)
        + 0.10 * immediate
        + 0.05 * (not purchase)
        + 0.05 * (not paid),
        2,
    )
    failures: list[str] = []
    if not action_verb:
        failures.append("missing observable action verb")
    if not subject:
        failures.append("missing required subject")
    if action_verb and not required_object:
        failures.append("missing required action object")
    if any(_ascii_key(term) in key for term in _VAGUE_ONLY_TERMS) and not action_verb:
        failures.append("vague motivational advice lacks a concrete observable action")
    if not immediate:
        failures.append("action is not immediately performable")
    if purchase:
        failures.append("action requires a purchase")
    if paid:
        failures.append("action requires a paid service")
    if score < _MINIMUM_SPECIFICITY_SCORE:
        failures.append(
            f"practical specificity {score:.2f} is below {_MINIMUM_SPECIFICITY_SCORE:.2f}"
        )
    return ActionMetadata(
        action_verb=action_verb,
        required_subject=subject,
        required_object=required_object,
        action_condition=condition,
        action_scope=action_scope,
        estimated_effort=estimated_effort,
        immediate_action_possible=immediate,
        requires_purchase=purchase,
        requires_paid_service=paid,
        specificity_score=score,
        failure_reasons=tuple(dict.fromkeys(failures)),
    )


def _stamp_actionability(scene: Scene) -> None:
    metadata = _action_metadata(scene.voice_script)
    scene.action_verb = metadata.action_verb
    scene.required_subject = metadata.required_subject
    scene.required_object = metadata.required_object
    scene.action_condition = metadata.action_condition
    scene.action_scope = metadata.action_scope
    scene.estimated_effort = metadata.estimated_effort
    scene.immediate_action_possible = metadata.immediate_action_possible
    scene.requires_purchase = metadata.requires_purchase
    scene.requires_paid_service = metadata.requires_paid_service
    scene.practical_specificity_score = metadata.specificity_score
    scene.actionability_failure_reasons = list(metadata.failure_reasons)
    scene.actionability_status = "passed" if metadata.is_actionable else "failed"


def _step_similarity(first: Scene, second: Scene) -> float:
    scope_score = 0.55 if first.action_scope == second.action_scope else 0.0
    object_score = 0.30 * _jaccard(
        _semantic_object_tokens(first.required_object),
        _semantic_object_tokens(second.required_object),
    )
    condition_score = 0.15 * _jaccard(
        set(_ascii_key(first.action_condition).split()),
        set(_ascii_key(second.action_condition).split()),
    )
    return round(scope_score + object_score + condition_score, 3)


def _stamp_duplicate_diagnostics(plan: TellaScenePlan, steps: list[Scene]) -> None:
    pairwise: dict[str, float] = {}
    duplicate_pairs: list[str] = []
    duplicate_edges: list[tuple[int, int]] = []
    for first, second in combinations(steps, 2):
        key = f"{first.step_number}-{second.step_number}"
        score = _step_similarity(first, second)
        pairwise[key] = score
        if score >= _DUPLICATE_STEP_THRESHOLD:
            duplicate_pairs.append(key)
            duplicate_edges.append((first.step_number, second.step_number))
    for scene in steps:
        scene.duplicate_step_score = max(
            (
                score
                for pair, score in pairwise.items()
                if str(scene.step_number) in pair.split("-")
            ),
            default=0.0,
        )
    plan.pairwise_step_similarity = pairwise
    plan.maximum_duplicate_step_score = max(pairwise.values(), default=0.0)
    plan.duplicate_step_pairs = duplicate_pairs
    plan.distinct_step_count = _distinct_component_count(steps, duplicate_edges)
    plan.duplicate_step_validation_status = (
        "passed"
        if len(steps) == 3 and plan.distinct_step_count == 3
        else "failed"
    )


def _stamp_safety_diagnostics(plan: TellaScenePlan, scenes: list[Scene]) -> None:
    plan_failures: list[str] = []
    unsupported: list[str] = []
    high_stakes = False
    for scene in scenes:
        key = _ascii_key(scene.voice_script)
        failures: list[str] = []
        scene_unsupported = [
            term for term in _UNSUPPORTED_CLAIM_TERMS if _ascii_key(term) in key
        ]
        stakes = [term for term in _HIGH_STAKES_TERMS if _ascii_key(term) in key]
        unsafe = [term for term in _UNSAFE_TERMS if _ascii_key(term) in key]
        if stakes:
            failures.append(
                "high-stakes medical, legal, financial, or self-harm advice requires professional handling"
            )
        if unsafe:
            failures.append("unsafe confrontation, surveillance, deception, or manipulation advice")
        if scene_unsupported:
            failures.append("unsupported guaranteed or private-knowledge claim")
        scene.high_stakes_advice_detected = bool(stakes)
        scene.unsupported_claims = scene_unsupported
        scene.safety_failure_reasons = list(dict.fromkeys(failures))
        scene.safety_status = "passed" if not failures else "failed"
        plan_failures.extend(failures)
        unsupported.extend(scene_unsupported)
        high_stakes = high_stakes or bool(stakes)
    plan.safety_failure_reasons = list(dict.fromkeys(plan_failures))
    plan.unsupported_claims = list(dict.fromkeys(unsupported))
    plan.high_stakes_advice_detected = high_stakes
    plan.safety_status = "passed" if not plan.safety_failure_reasons else "failed"


def _stamp_naturalness(scenes: list[Scene]) -> None:
    step_prefixes: list[tuple[str, Scene]] = []
    for scene in scenes:
        failures = _naturalness_errors(scene.voice_script)
        scene.naturalness_failure_reasons = failures
        scene.vietnamese_naturalness_failure_reasons = failures
        scene.vietnamese_naturalness_status = "passed" if not failures else "failed"
        if scene.scene_role == "practical_step":
            prefix = " ".join(_ascii_key(scene.voice_script).split()[:2])
            step_prefixes.append((prefix, scene))
    for first, second in combinations(step_prefixes, 2):
        if first[0] and first[0] == second[0]:
            reason = "practical steps repeat the same mechanical sentence opening"
            for scene in (first[1], second[1]):
                if reason not in scene.naturalness_failure_reasons:
                    scene.naturalness_failure_reasons.append(reason)
                    scene.vietnamese_naturalness_failure_reasons.append(reason)
                    scene.vietnamese_naturalness_status = "failed"


def _stamp_visual_metadata(scenes: list[Scene]) -> None:
    role_visuals = {
        "hook": ("adult encountering one specific everyday obstacle", "everyday obstacle"),
        "context": ("adult noticing several competing cues", "abstract distraction cues"),
        "context_part_one": ("adult noticing one cause", "simple cause symbol"),
        "context_part_two": ("adult noticing a second cause", "second cause symbol"),
        "common_mistake": ("adult repeating an ineffective pattern", "repeated action objects"),
        "correction": ("adult correcting one visible action", "corrected everyday object"),
        "today_action": ("adult beginning one small action", "one prepared everyday object"),
        "closing": ("adult beside one completed small action", "completed everyday object"),
    }
    for scene in scenes:
        scene.visual_text_required = False
        scene.visual_environment = "simple everyday setting with blank icon-based surfaces"
        if scene.scene_role == "practical_step":
            scene.visual_object = _safe_visual_object(
                scene.required_object,
                scene.action_scope,
            )
            scene.visual_environment = _safe_visual_environment(scene.action_scope)
            condition = _safe_visual_condition(scene)
            action_phrase = _VISUAL_ACTION_PHRASES.get(
                scene.action_verb,
                "performing one clear action with",
            )
            scene.visual_action = " ".join(
                part
                for part in (
                    "visible adult",
                    action_phrase,
                    scene.visual_object,
                    condition,
                )
                if part
            )
            _validate_visual_metadata(scene)
        else:
            scene.visual_action, scene.visual_object = role_visuals.get(
                scene.scene_role,
                ("adult performing one clear practical action", "simple everyday object"),
            )
            today_metadata = _action_metadata(scene.voice_script)
            if scene.scene_role in {"today_action", "correction"} and today_metadata.action_verb:
                scene.visual_object = _safe_visual_object(
                    today_metadata.required_object,
                    today_metadata.action_scope,
                )
                scene.visual_environment = _safe_visual_environment(
                    today_metadata.action_scope
                )
                scene.visual_action = " ".join(
                    part
                    for part in (
                        "visible adult",
                        _VISUAL_ACTION_PHRASES.get(
                            today_metadata.action_verb,
                            "performing one clear action with",
                        ),
                        scene.visual_object,
                        _safe_visual_condition_values(
                            today_metadata.required_object,
                            today_metadata.action_condition,
                        ),
                    )
                    if part
                )
            scene.visual_metadata_status = "not_applicable"
        scene.image_prompt = (
            f"planner-only wordless practical composition, {scene.visual_action}, "
            f"{scene.visual_object}, blank icon-based surfaces"
        )


def _stamp_overlap_diagnostics(plan: TellaScenePlan, scenes: list[Scene]) -> None:
    actionable_count = sum(
        scene.scene_role == "practical_step" and scene.actionability_status == "passed"
        for scene in scenes
    )
    actionable_count += sum(
        scene.scene_role in {"today_action", "correction"}
        and _action_metadata(scene.voice_script).is_actionable
        for scene in scenes
    )
    reflective_count = 0
    harsh_count = 0
    abstract_count = 0
    for scene in scenes:
        key = _ascii_key(scene.voice_script)
        has_action = bool(_action_metadata(scene.voice_script).action_verb)
        if any(_ascii_key(term) in key for term in _EMOTIONAL_TERMS) and not has_action:
            reflective_count += 1
        if any(_ascii_key(term) in key for term in _HARSH_TRUTH_TERMS) and not has_action:
            harsh_count += 1
        if any(_ascii_key(term) in key for term in _VAGUE_ONLY_TERMS) and not has_action:
            abstract_count += 1
    count = max(1, len(scenes))
    action_density = round(actionable_count / count, 3)
    reflection_ratio = round(reflective_count / count, 3)
    harsh_ratio = round(harsh_count / count, 3)
    abstract_ratio = round(abstract_count / count, 3)
    plan.practical_action_density = action_density
    plan.reflective_statement_ratio = reflection_ratio
    plan.harsh_truth_statement_ratio = harsh_ratio
    plan.abstract_motivation_ratio = abstract_ratio
    plan.emotional_symbolic_overlap_score = round(
        min(1.0, reflection_ratio + 0.5 * abstract_ratio),
        3,
    )
    plan.life_insight_symbolic_overlap_score = round(
        min(1.0, harsh_ratio + 0.5 * reflection_ratio),
        3,
    )
    failures: list[str] = []
    if action_density < _MINIMUM_ACTION_DENSITY:
        failures.append(
            f"practical action density {action_density:.3f} is below {_MINIMUM_ACTION_DENSITY:.2f}"
        )
    if reflection_ratio > _MAXIMUM_REFLECTION_RATIO:
        failures.append("emotional reflection dominates concrete instruction")
    if plan.life_insight_symbolic_overlap_score > _MAXIMUM_LIFE_INSIGHT_OVERLAP:
        failures.append("plan reads primarily as life-insight observation")
    if abstract_ratio > _MAXIMUM_ABSTRACT_MOTIVATION_RATIO:
        failures.append("abstract motivation dominates observable action")
    if plan.distinct_step_count < 3:
        failures.append("plan contains fewer than three distinct actionable steps")
    plan.overlap_failure_reasons = failures
    plan.overlap_validation_status = "passed" if not failures else "failed"


def _roles_for_segments(segments: list[str]) -> tuple[str, ...]:
    if len(segments) == 7:
        return _ROLES_7
    if len(segments) != 8:
        return ()
    seventh = _ascii_key(segments[6])
    eighth = _ascii_key(segments[7])
    if any(term in seventh for term in ("thay vao do", "sua lai", "dieu chinh")):
        return _ROLES_8_CORRECTION
    if any(term in eighth for term in ("ket lai", "ghi nho", "dieu quan trong")):
        return _ROLES_8_CLOSING
    return _ROLES_8_CONTEXT


def _role_title(role: str, scene_index: int, roles: tuple[str, ...]) -> str:
    if role == "practical_step":
        return f"Practical Step {sum(1 for item in roles[:scene_index] if item == role)}"
    return role.replace("_", " ").title()


def _script_segments(script: str) -> list[str]:
    raw = (script or "").strip()
    if not raw:
        raise ValueError("practical life steps script is empty")
    return [
        re.sub(r"\s+", " ", line).strip()
        for line in raw.splitlines()
        if line.strip()
    ]


def _primary_complete_sentence(text: str) -> str:
    parts = [
        item.strip()
        for item in re.split(r"(?<=[.!?…])\s+", (text or "").strip())
        if item.strip()
    ]
    return parts[0] if len(parts) > 1 else (text or "").strip()


def _semantic_fit_candidate(text: str, role: str) -> tuple[str, list[str]]:
    source = (text or "").strip()
    sentences = [
        item.strip()
        for item in re.split(r"(?<=[.!?…])\s+", source)
        if item.strip()
    ]
    if len(sentences) < 2:
        return source, []
    if role != "practical_step":
        return sentences[0], ["remove_redundant_secondary_sentence"]

    analyzed = [(_action_metadata(sentence), index, sentence) for index, sentence in enumerate(sentences)]
    actionable = [item for item in analyzed if item[0].is_actionable]
    if not actionable:
        return source, []
    metadata, selected_index, selected = max(
        actionable,
        key=lambda item: (
            item[0].specificity_score,
            bool(item[0].action_condition),
            len(item[0].required_object),
            -item[1],
        ),
    )
    if selected_index == 0:
        return selected, ["remove_redundant_secondary_sentence"]

    operations = ["preserve_secondary_action_object"]
    if metadata.action_condition:
        operations.append("merge_action_with_condition")
    operations.extend(
        ("retain_distinguishing_detail", "simplify_explanatory_clause")
    )
    return selected, operations


def _naturalness_errors(text: str) -> list[str]:
    source = (text or "").strip()
    key = _ascii_key(source)
    words = key.split()
    errors: list[str] = []
    if len(words) < 5:
        errors.append("narration is an overly compressed fragment")
    if source and source[-1] not in ".!?…":
        errors.append("narration lacks complete sentence punctuation")
    if words and words[-1] in {_ascii_key(word) for word in _DANGLING_WORDS}:
        errors.append("narration ends with a dangling conjunction")
    if not any(_ascii_key(term) in key for term in _PREDICATE_TERMS):
        errors.append("narration lacks a complete predicate")
    return errors


def _safe_visual_object(value: str, action_scope: str = "") -> str:
    key = _ascii_key(value)
    if any(term in key for term in ("dien thoai", "may tinh", "thiet bi")):
        return "a generic phone with an unreadable icon-only screen"
    if "lich" in key:
        return "an abstract calendar grid with blank cells"
    if "tin nhan" in key:
        return "blank message cards with abstract marks"
    if any(
        term in key
        for term in ("lich", "ghi chu", "tin nhan", "the", "cau", "viet", "ghi")
    ):
        return "a blank note card with abstract marks"
    if any(term in key for term in ("tai lieu", "sach", "giay", "dung cu")):
        return "books and loose study papers"
    if action_scope == "time_blocking" or any(term in key for term in ("phut", "gio")):
        return "abstract timer shapes without numerals"
    if action_scope == "boundary_setting":
        return "a simple open doorway and boundary marker"
    return "one simple everyday object"


def _safe_visual_condition(scene: Scene) -> str:
    return _safe_visual_condition_values(
        scene.required_object,
        scene.action_condition,
    )


def _safe_visual_condition_values(required_object: str, action_condition: str) -> str:
    object_key = _ascii_key(required_object)
    condition_key = _ascii_key(action_condition)
    parts: list[str] = []
    if "ngoai tam tay" in object_key:
        parts.append("beyond arm's reach")
    if any(term in condition_key for term in ("truoc khi", "truoc phien")):
        parts.append("before a focused work session")
    if any(term in condition_key for term in ("phut", "gio", "khoang")):
        parts.append("for a short work interval")
    return " ".join(parts)


def _safe_visual_environment(action_scope: str) -> str:
    return {
        "externalize_information": "quiet desk with blank paper surfaces",
        "notification_control": "simple distraction-free workspace",
        "environment_control": "simple distraction-free workspace",
        "preparation": "organized desk with controlled negative space",
        "time_blocking": "calm workspace with an abstract timer",
        "boundary_setting": "simple room with an open doorway",
    }.get(action_scope, "simple everyday workspace with controlled negative space")


def _validate_visual_metadata(scene: Scene) -> None:
    visual = (scene.visual_action or "").strip()
    visual_lower = visual.casefold()
    metadata_blob = " ".join(
        (scene.visual_action, scene.visual_object, scene.visual_environment)
    ).strip()
    metadata_lower = metadata_blob.casefold()
    expected_verb = _VISUAL_ACTION_PHRASES.get(scene.action_verb, "").split(" ", 1)[0]
    condition = _safe_visual_condition(scene)
    scene.visual_action_subject_present = bool(
        re.search(r"\b(?:adult|person)\b", visual_lower)
    )
    scene.visual_action_verb_present = bool(
        expected_verb and re.search(rf"\b{re.escape(expected_verb)}\b", visual_lower)
    )
    scene.visual_action_object_present = bool(
        scene.visual_object and scene.visual_object.casefold() in visual_lower
    )
    scene.visual_action_condition_preserved = bool(
        not condition or condition.casefold() in visual_lower
    )
    scene.visual_action_language_consistent = bool(
        metadata_blob and all(ord(char) < 128 for char in metadata_blob)
    )
    scene.visual_action_provider_safe = not re.search(r"\d", metadata_blob) and not any(
        re.search(pattern, metadata_lower)
        for pattern in _VISUAL_PROVIDER_UNSAFE_PATTERNS
    )
    checks = (
        (scene.visual_action_subject_present, "visible adult subject is missing"),
        (scene.visual_action_verb_present, "normalized observable action is missing"),
        (scene.visual_action_object_present, "normalized action object is missing"),
        (scene.visual_action_condition_preserved, "visual action condition was not preserved"),
        (scene.visual_action_language_consistent, "visual action language is inconsistent"),
        (scene.visual_action_provider_safe, "visual action is not provider-safe"),
    )
    scene.visual_metadata_failure_reasons = [
        reason for passed, reason in checks if not passed
    ]
    scene.visual_metadata_status = (
        "passed" if not scene.visual_metadata_failure_reasons else "failed"
    )


def _semantic_object_tokens(text: str) -> set[str]:
    key = _ascii_key(text)
    aliases = (
        (("ke hoach", "danh sach", "viec can lam", "nhiem vu"), "task_plan"),
        (("thong bao",), "notification"),
        (("dien thoai", "thiet bi"), "device"),
        (("tai lieu", "dung cu"), "materials"),
        (("ranh gioi", "gioi han"), "boundary"),
    )
    tokens = set(key.split())
    for phrases, canonical in aliases:
        if any(phrase in key for phrase in phrases):
            tokens.add(canonical)
    stop = {"mot", "nhung", "cac", "cua", "cho", "va", "de", "ban", "ra"}
    return tokens - stop


def _jaccard(first: set[str], second: set[str]) -> float:
    if not first or not second:
        return 0.0
    return len(first & second) / len(first | second)


def _distinct_component_count(
    steps: list[Scene],
    duplicate_edges: list[tuple[int, int]],
) -> int:
    parents = {scene.step_number: scene.step_number for scene in steps}

    def find(value: int) -> int:
        while parents[value] != value:
            parents[value] = parents[parents[value]]
            value = parents[value]
        return value

    for first, second in duplicate_edges:
        root_first = find(first)
        root_second = find(second)
        if root_first != root_second:
            parents[root_second] = root_first
    return len({find(value) for value in parents})


def _estimated_total(texts: list[str], voice_rate: str) -> float:
    return round(
        sum(
            _estimate_scene_duration(_word_count(text), index, voice_rate)
            for index, text in enumerate(texts)
        ),
        2,
    )


def _estimate_scene_duration(word_count: int, index: int, voice_rate: str) -> float:
    duration = word_count / _words_per_second(voice_rate)
    if index > 0:
        duration += 0.35
    return round(duration, 2)


def _words_per_second(voice_rate: str) -> float:
    percent = int(normalize_voice_rate(voice_rate).rstrip("%"))
    return max(1.5, 3.0 * (100 + percent) / 98.0)


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[^\W_]+\b", text or "", flags=re.UNICODE))


def _ascii_key(text: str) -> str:
    raw = (text or "").casefold().replace("đ", "d")
    decomposed = unicodedata.normalize("NFKD", raw)
    ascii_only = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", ascii_only).strip()


__all__ = [
    "enforce_practical_life_steps_plan",
    "plan_practical_life_steps_from_script",
    "plan_practical_life_steps_from_topic",
]
