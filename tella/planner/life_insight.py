"""Deterministic planner contract for the life-insight symbolic recipe."""
from __future__ import annotations

import re
from typing import Any

from tella._voice_pace import VoicePace, normalize_voice_rate
from tella.planner.models import Scene, TellaScenePlan
from tella.planner.life_insight_visuals import apply_life_insight_visuals
from tella.planner.voices import edge_voice_for

_ROLES_8 = (
    "hook",
    "behavior",
    "false_belief",
    "underlying_truth",
    "concrete_sign",
    "consequence",
    "mature_perspective",
    "conclusion",
)
_ROLES_7 = (
    "hook",
    "behavior",
    "false_belief",
    "underlying_truth_and_concrete_sign",
    "consequence",
    "mature_perspective",
    "conclusion",
)

_CLAIM_TYPES = {
    "hook": "uncomfortable_question",
    "behavior": "observation",
    "false_belief": "self_explanation",
    "underlying_truth": "interpretation",
    "concrete_sign": "observable_evidence",
    "underlying_truth_and_concrete_sign": "evidence_based_interpretation",
    "consequence": "consequence",
    "mature_perspective": "reframe",
    "conclusion": "takeaway",
}
_EMOTIONAL_FUNCTIONS = {
    "hook": "create useful discomfort",
    "behavior": "make the familiar pattern visible",
    "false_belief": "name the comforting explanation",
    "underlying_truth": "introduce the stronger interpretation",
    "concrete_sign": "ground the interpretation in observable behavior",
    "underlying_truth_and_concrete_sign": "connect interpretation to evidence",
    "consequence": "show the cost of continuing the pattern",
    "mature_perspective": "replace guessing with a mature standard",
    "conclusion": "leave one concise useful truth",
}
_TRANSITIONS = {
    "hook": "move from question to familiar behavior",
    "behavior": "separate behavior from its comforting explanation",
    "false_belief": "challenge the explanation without cruelty",
    "underlying_truth": "move from interpretation to observable evidence",
    "concrete_sign": "connect evidence to its likely consequence",
    "underlying_truth_and_concrete_sign": "connect evidence to consequence",
    "consequence": "open a more mature perspective",
    "mature_perspective": "prepare the final takeaway",
    "conclusion": "close on the strongest concise insight",
}
_INSIGHT_STRENGTH = {
    "hook": "moderate",
    "behavior": "moderate",
    "false_belief": "moderate",
    "underlying_truth": "strong",
    "concrete_sign": "strong",
    "underlying_truth_and_concrete_sign": "strong",
    "consequence": "strong",
    "mature_perspective": "strong",
    "conclusion": "strong",
}

_EMOTIONAL_TERMS = (
    "buon",
    "co don",
    "met moi",
    "dau long",
    "ton thuong",
    "trong rong",
    "sad",
    "lonely",
    "hurt",
    "tired",
)
_INSIGHT_TERMS = (
    "su that",
    "cho thay",
    "dau hieu",
    "khong phai",
    "nghia la",
    "nhat quan",
    "hanh dong",
    "neu tiep tuc",
    "hay nhin",
    "tran trong",
    "truth",
    "evidence",
    "consequence",
    "pattern",
    "takeaway",
)
_PRIVATE_THOUGHT_PATTERNS = (
    "chac chan ho nghi",
    "trong long ho",
    "ho luon nghi",
    "biet ro ho muon",
)
_CRUEL_OR_DIAGNOSTIC_TERMS = (
    "ngu ngoc",
    "vo dung",
    "dang thuong",
    "chac chan la ke",
    "roi loan tam ly",
)
_FILLERS = ("that ra", "doi khi", "co nhung")
_DURATION_TARGET_SECONDS = 35.0
_PREFERRED_DURATION_RANGE = (34.0, 36.0)
_HARD_DURATION_RANGE = (32.0, 38.0)
_MAX_SCENE_COMPRESSION_RATIO = 0.40
_ROLE_TARGET_WORDS = {
    "hook": 12,
    "behavior": 12,
    "false_belief": 10,
    "underlying_truth": 14,
    "concrete_sign": 14,
    "underlying_truth_and_concrete_sign": 17,
    "consequence": 16,
    "mature_perspective": 14,
    "conclusion": 16,
}
_DANGLING_CONJUNCTIONS = {"va", "nhung", "vi", "khi", "neu"}
_DEFAULT_RECIPE_PACE = VoicePace(
    name="custom",
    edge_rate="-5%",
    google_rate=0.95,
)


def plan_life_insight_from_script(
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
    del seed  # The planner is deterministic; retained for a stable future API.
    segments = _script_segments(user_script)
    if len(segments) not in {7, 8}:
        raise ValueError(
            "life_insight_symbolic requires exactly 7 or 8 narration segments; "
            f"received {len(segments)}"
        )
    if target_lang != "vi":
        raise ValueError("life_insight_symbolic_v1 currently requires Vietnamese narration")
    roles = _ROLES_8 if len(segments) == 8 else _ROLES_7
    pace = voice_pace or _DEFAULT_RECIPE_PACE
    gender = (voice_gender or "male").lower()
    scenes = [
        Scene(
            scene_index=index,
            kind="scene",
            title=role.replace("_", " ").title(),
            voice_script=text,
            image_prompt=(
                "planner-only life insight visual placeholder, one readable "
                f"meaning for role {role}"
            ),
            stock_query="life insight symbol",
            scene_meaning=text,
            visual_mode="life_insight_planner_only",
        )
        for index, (role, text) in enumerate(zip(roles, segments), start=1)
    ]
    plan = TellaScenePlan(
        title=segments[0][:120],
        language="vi",
        aspect_ratio=aspect_ratio,
        media_source=media_source,
        duration_mode=duration_mode,
        theme="life_insight_symbolic",
        voice_pace_name=pace.name,
        voice_edge_rate=pace.edge_rate,
        voice_google_rate=pace.google_rate,
        voice_gender=gender,
        voice_name=edge_voice_for("vi", gender),
        subtitle_style="insight_reel",
        tts_continuous=True,
        tts_text_source="global_narration_text",
        global_narration_text=" ".join(segments),
        scenes=scenes,
    )
    enforce_life_insight_plan(plan)
    return apply_life_insight_visuals(plan)


def plan_life_insight_from_topic(
    *,
    topic: str,
    target_lang: str,
    **kwargs: Any,
) -> TellaScenePlan:
    if target_lang != "vi":
        raise ValueError("life_insight_symbolic_v1 currently requires Vietnamese narration")
    topic_text = re.sub(r"\s+", " ", (topic or "một hành vi quen thuộc").strip())
    script = "\n".join(
        (
            "Một câu hỏi khó chịu: mẫu hành vi này đang lặp lại điều gì?",
            "Bạn phản ứng ngay, rồi hy vọng cảm giác ấy chứng minh giá trị.",
            "Cách giải thích dễ tin: vài khoảnh khắc đủ nói lên tất cả.",
            "Sự thật: lựa chọn nhất quán đáng tin hơn lời giải thích.",
            "Dấu hiệu rõ: hành động vẫn tiếp tục khi hoàn cảnh đổi khác.",
            "Nếu bỏ qua, bạn sẽ đầu tư vào kỳ vọng thiếu căn cứ.",
            "Hãy quan sát hành vi trước khi diễn giải mọi ý định.",
            "Điều có giá trị sẽ hiện diện nhất quán, không chỉ khi thuận tiện.",
        )
    )
    plan = plan_life_insight_from_script(
        user_script=script,
        target_lang=target_lang,
        **kwargs,
    )
    plan.title = topic_text[:120]
    return plan


def enforce_life_insight_plan(plan: TellaScenePlan) -> None:
    body_scenes = [scene for scene in plan.scenes if scene.kind == "scene"]
    roles = _ROLES_8 if len(body_scenes) == 8 else _ROLES_7 if len(body_scenes) == 7 else ()
    errors: list[str] = []
    if not roles:
        errors.append(
            f"life insight plan must contain 7 or 8 scenes, got {len(body_scenes)}"
        )
    if roles:
        body_scenes, roles = _prepare_and_fit_narration(plan, body_scenes, roles)
        if plan.narration_fit_status == "failed":
            errors.append(
                "narration fitting failed: " + plan.narration_fit_failure_reason
            )

    errors.extend(_language_quality_errors(body_scenes))

    emotional_only = 0
    for index, scene in enumerate(body_scenes):
        role = roles[index] if roles else ""
        key = _ascii_key(scene.voice_script)
        if any(term in key for term in _EMOTIONAL_TERMS) and not any(
            term in key for term in _INSIGHT_TERMS
        ):
            emotional_only += 1
        _stamp_scene_metadata(scene, role, index, plan.voice_edge_rate)

    overlap_score = round(emotional_only / max(1, len(body_scenes)), 3)
    plan.recipe_overlap_score = overlap_score
    plan.recipe_overlap_detected = overlap_score >= 0.5
    plan.overlap_repair_applied = False
    if plan.recipe_overlap_detected:
        errors.append(
            "plan overlaps emotional reflection: most scenes describe feelings "
            "without insight, evidence, consequence, or mature takeaway"
        )

    total_estimated = round(
        sum(scene.estimated_duration_seconds for scene in body_scenes),
        2,
    )
    plan.life_insight_estimated_duration_seconds = total_estimated
    if total_estimated < 32:
        errors.append(f"estimated duration {total_estimated:.2f}s is below 32s")
    if total_estimated > 38:
        errors.append(f"estimated duration {total_estimated:.2f}s exceeds 38s")
    for scene in body_scenes:
        maximum = _maximum_scene_duration(scene.scene_role)
        if scene.estimated_duration_seconds < 3.6 or scene.estimated_duration_seconds > maximum:
            errors.append(
                f"scene {scene.scene_index} estimated duration "
                f"{scene.estimated_duration_seconds:.2f}s is outside the normal range"
            )

    if roles:
        if not any(scene.observable_evidence for scene in body_scenes):
            errors.append("life insight plan requires concrete observable evidence")
        conclusion = body_scenes[-1]
        if conclusion.scene_role != "conclusion" or conclusion.insight_strength != "strong":
            errors.append("life insight plan requires a strong mature takeaway")

    plan.life_insight_validation_errors = list(dict.fromkeys(errors))
    plan.life_insight_validation_status = "passed" if not errors else "failed"
    if errors:
        raise ValueError("life insight plan validation failed: " + "; ".join(errors))


def _stamp_scene_metadata(
    scene: Scene,
    role: str,
    index: int,
    voice_rate: str,
) -> None:
    word_count = _word_count(scene.voice_script)
    duration = word_count / _words_per_second(voice_rate)
    if index > 0:
        duration += 0.35
    if role == "conclusion":
        duration += 0.15
    scene.scene_role = role
    scene.claim_type = _CLAIM_TYPES.get(role, "")
    scene.emotional_function = _EMOTIONAL_FUNCTIONS.get(role, "")
    scene.observable_evidence = (
        scene.voice_script
        if role in {"behavior", "concrete_sign", "underlying_truth_and_concrete_sign"}
        else ""
    )
    scene.insight_strength = _INSIGHT_STRENGTH.get(role, "")
    scene.narration_word_count = word_count
    scene.estimated_duration_seconds = round(duration, 2)
    scene.fitted_narration_word_count = word_count
    scene.fitted_estimated_duration_seconds = round(duration, 2)
    scene.transition_purpose = _TRANSITIONS.get(role, "")
    scene.conclusion_dependency = (
        "mature_perspective" if role == "conclusion" else ""
    )
    scene.subtitle_highlight_words = _highlight_words(scene.voice_script)


def _prepare_and_fit_narration(
    plan: TellaScenePlan,
    scenes: list[Scene],
    roles: tuple[str, ...],
) -> tuple[list[Scene], tuple[str, ...]]:
    if not all(scene.original_voice_script for scene in scenes):
        for index, (scene, role) in enumerate(zip(scenes, roles)):
            original_words = _word_count(scene.voice_script)
            original_seconds = _estimate_scene_duration(
                original_words,
                index,
                role,
                plan.voice_edge_rate,
            )
            scene.original_voice_script = scene.voice_script
            scene.original_narration_word_count = original_words
            scene.original_estimated_duration_seconds = original_seconds

    original_words = sum(scene.original_narration_word_count for scene in scenes)
    original_seconds = round(
        sum(scene.original_estimated_duration_seconds for scene in scenes),
        2,
    )
    plan.original_total_word_count = original_words
    plan.original_estimated_duration_seconds = original_seconds
    plan.duration_target_seconds = _DURATION_TARGET_SECONDS
    plan.seven_scene_fallback_considered = False
    plan.seven_scene_fallback_applied = False

    if plan.narration_fit_status == "passed" and any(
        scene.narration_fit_applied for scene in scenes
    ):
        _finalize_fit_metadata(plan, scenes, roles)
        return scenes, roles

    overlong_scene = any(
        scene.original_estimated_duration_seconds
        > (5.5 if role == "conclusion" else 5.2)
        for scene, role in zip(scenes, roles)
    )
    plan.narration_fit_required = original_seconds > _HARD_DURATION_RANGE[1] or overlong_scene
    plan.narration_fit_applied = False
    plan.narration_fit_pass_count = 0
    plan.narration_fit_failure_reason = ""

    if not plan.narration_fit_required:
        plan.narration_fit_status = "not_required"
        for scene in scenes:
            scene.fitting_candidate_count = 1
            scene.selected_fitting_candidate_id = "original"
        _apply_final_surface_realization(scenes, roles)
        _finalize_fit_metadata(plan, scenes, roles)
        return scenes, roles

    changed = _fit_candidate_set(scenes, roles, allow_merged_over_limit=False)
    _apply_final_surface_realization(scenes, roles)
    plan.narration_fit_pass_count = 1
    plan.narration_fit_applied = changed
    _finalize_fit_metadata(plan, scenes, roles)

    if len(scenes) == 8 and (
        _fit_errors(plan, scenes, roles)
        or plan.fitted_estimated_duration_seconds > _PREFERRED_DURATION_RANGE[1]
    ):
        plan.seven_scene_fallback_considered = True
        scenes, roles = _merge_truth_and_evidence_scenes(scenes)
        plan.scenes = scenes
        changed = _fit_candidate_set(scenes, roles, allow_merged_over_limit=True)
        _apply_final_surface_realization(scenes, roles)
        plan.narration_fit_pass_count = 2
        plan.narration_fit_applied = plan.narration_fit_applied or changed
        _finalize_fit_metadata(plan, scenes, roles)
        if not _fit_errors(plan, scenes, roles):
            plan.seven_scene_fallback_applied = True

    fit_errors = _fit_errors(plan, scenes, roles)
    plan.narration_fit_status = "failed" if fit_errors else "passed"
    plan.narration_fit_failure_reason = "; ".join(fit_errors)
    plan.global_narration_text = " ".join(scene.voice_script for scene in scenes)
    return scenes, roles


def _finalize_fit_metadata(
    plan: TellaScenePlan,
    scenes: list[Scene],
    roles: tuple[str, ...],
) -> None:
    for index, (scene, role) in enumerate(zip(scenes, roles)):
        fitted_words = _word_count(scene.voice_script)
        fitted_seconds = _estimate_scene_duration(
            fitted_words,
            index,
            role,
            plan.voice_edge_rate,
        )
        scene.fitted_narration_word_count = fitted_words
        scene.fitted_estimated_duration_seconds = fitted_seconds
        fidelity = _evaluate_semantic_fidelity(
            scene.original_voice_script,
            scene.voice_script,
            role,
        )
        scene.required_semantic_anchors = fidelity["required_anchors"]
        scene.preserved_semantic_anchors = fidelity["preserved_anchors"]
        scene.missing_semantic_anchors = fidelity["missing_anchors"]
        scene.semantic_anchors_preserved = fidelity["preserved_anchors"]
        scene.semantic_anchor_loss_detected = bool(fidelity["missing_anchors"])
        scene.required_semantic_relations = fidelity["required_relations"]
        scene.preserved_semantic_relations = fidelity["preserved_relations"]
        scene.missing_semantic_relations = fidelity["missing_relations"]
        scene.semantic_relation_loss_detected = bool(fidelity["missing_relations"])
        evidence = _evaluate_evidence_fidelity(
            scene.original_voice_script,
            scene.voice_script,
            role,
        )
        scene.observable_claims = evidence["observable_claims"]
        scene.inferred_private_motives = evidence["inferred_private_motives"]
        scene.unsupported_inference_detected = evidence[
            "unsupported_inference_detected"
        ]
        scene.unsupported_inference_reasons = evidence[
            "unsupported_inference_reasons"
        ]
        scene.evidence_condition_complete = evidence[
            "evidence_condition_complete"
        ]
        naturalness_errors = _vietnamese_naturalness_errors(scene.voice_script)
        surface = _evaluate_surface_quality(scene.voice_script, role)
        scene.surface_quality_status = surface["status"]
        scene.surface_quality_failure_reasons = surface["failure_reasons"]
        scene.predicate_complete = surface["predicate_complete"]
        scene.complement_complete = surface["complement_complete"]
        scene.clause_connection_natural = surface["clause_connection_natural"]
        scene.pronoun_reference_clear = surface["pronoun_reference_clear"]
        scene.vietnamese_naturalness_failure_reasons = list(
            dict.fromkeys([*naturalness_errors, *surface["failure_reasons"]])
        )
        scene.vietnamese_naturalness_status = (
            "passed"
            if not naturalness_errors and surface["status"] == "passed"
            else "failed"
        )
        scene.scene_compression_ratio = round(
            max(0, scene.original_narration_word_count - fitted_words)
            / max(1, scene.original_narration_word_count),
            4,
        )
        scene.compression_limit_exceeded = (
            scene.scene_compression_ratio > _MAX_SCENE_COMPRESSION_RATIO
        )

    plan.fitted_total_word_count = sum(
        scene.fitted_narration_word_count for scene in scenes
    )
    plan.fitted_estimated_duration_seconds = round(
        sum(scene.fitted_estimated_duration_seconds for scene in scenes),
        2,
    )
    plan.duration_reduction_seconds = round(
        plan.original_estimated_duration_seconds
        - plan.fitted_estimated_duration_seconds,
        2,
    )
    plan.duration_reduction_ratio = round(
        plan.duration_reduction_seconds
        / max(plan.original_estimated_duration_seconds, 0.01),
        4,
    )
    plan.maximum_scene_compression_ratio = max(
        (scene.scene_compression_ratio for scene in scenes),
        default=0.0,
    )
    plan.semantic_fidelity_status = (
        "failed"
        if any(
            scene.semantic_anchor_loss_detected
            or scene.semantic_relation_loss_detected
            or scene.unsupported_inference_detected
            or not scene.evidence_condition_complete
            for scene in scenes
        )
        else "passed"
    )
    plan.vietnamese_naturalness_status = (
        "failed"
        if any(scene.vietnamese_naturalness_status == "failed" for scene in scenes)
        else "passed"
    )
    plan.final_surface_failure_count = sum(
        scene.surface_quality_status == "failed" for scene in scenes
    )
    plan.final_surface_validation_status = (
        "passed" if plan.final_surface_failure_count == 0 else "failed"
    )
    plan.final_surface_repairs_applied = sum(
        scene.final_surface_repair_applied for scene in scenes
    )
    if not plan.narration_fit_required:
        plan.narration_fit_status = "not_required"
    plan.global_narration_text = " ".join(scene.voice_script for scene in scenes)


def _fit_candidate_set(
    scenes: list[Scene],
    roles: tuple[str, ...],
    *,
    allow_merged_over_limit: bool,
) -> bool:
    changed = False
    for scene, role in zip(scenes, roles):
        candidates = _fitting_candidates(scene.original_voice_script, role)
        scene.fitting_candidate_count = len(candidates)
        scored: list[tuple[tuple[float, ...], str, str, list[str]]] = []
        for candidate_id, text, operations in candidates:
            evidence = _evaluate_evidence_fidelity(
                scene.original_voice_script,
                text,
                role,
            )
            fidelity = _evaluate_semantic_fidelity(
                scene.original_voice_script,
                text,
                role,
            )
            naturalness_errors = _vietnamese_naturalness_errors(text)
            ratio = max(
                0.0,
                (_word_count(scene.original_voice_script) - _word_count(text))
                / max(1, _word_count(scene.original_voice_script)),
            )
            limit_allowed = allow_merged_over_limit and role == (
                "underlying_truth_and_concrete_sign"
            )
            semantically_safe = not fidelity["missing_anchors"] and not fidelity[
                "missing_relations"
            ]
            if (
                not evidence["evidence_condition_complete"]
                or evidence["unsupported_inference_detected"]
                or not semantically_safe
                or naturalness_errors
                or (ratio > _MAX_SCENE_COMPRESSION_RATIO and not limit_allowed)
            ):
                continue
            target = _ROLE_TARGET_WORDS[role]
            score = (
                0 if evidence["evidence_condition_complete"] else 1,
                len(evidence["inferred_private_motives"]),
                abs(_word_count(text) - target),
                _word_count(text),
                ratio,
            )
            scored.append((score, candidate_id, text, operations))

        if not scored:
            selected_id, selected_text, operations = "original", scene.original_voice_script, []
        else:
            _, selected_id, selected_text, operations = min(
                scored,
                key=lambda item: (item[0], item[1]),
            )
        scene.voice_script = selected_text
        scene.selected_fitting_candidate_id = selected_id
        scene.narration_fit_operations = operations
        scene.narration_fit_applied = selected_text != scene.original_voice_script
        changed = changed or scene.narration_fit_applied
    return changed


def _fitting_candidates(source: str, role: str) -> list[tuple[str, str, list[str]]]:
    candidates: list[tuple[str, str, list[str]]] = [("original", source, [])]

    def add(candidate_id: str, text: str, operations: list[str]) -> None:
        text = _clean_sentence(text)
        if text and all(existing[1] != text for existing in candidates):
            candidates.append((candidate_id, text, operations))

    concise = re.sub(r"\b(thật sự|rất)\b", "", source, flags=re.IGNORECASE)
    add("remove_intensifiers", concise, ["remove_filler_and_intensifier"])
    key = _ascii_key(source)

    if role == "hook" and "kieu quan tam" in key:
        text = re.sub(r"^Có một\s+", "", source, flags=re.IGNORECASE)
        add(
            "hook_remove_empty_intro",
            text,
            ["remove_empty_intro"],
        )
        text = re.sub(r"khi người ta cần", "khi họ cần", text, flags=re.IGNORECASE)
        add(
            "hook_concise_actor_reference",
            text,
            ["remove_empty_intro", "simplify_actor_reference"],
        )
    elif role == "behavior" and "tim den" in key:
        text = re.sub(r"^Mỗi lần gặp", "Khi gặp", source, flags=re.IGNORECASE)
        text = re.sub(r"cảm thấy mình rất", "cảm thấy mình", text, flags=re.IGNORECASE)
        text = re.sub(r"cảm thấy mình", "thấy mình", text, flags=re.IGNORECASE)
        add("behavior_keep_action_effect", text, ["simplify_repeated_context"])
        text = re.sub(r"tìm đến và khiến", "tìm đến, khiến", text, flags=re.IGNORECASE)
        add(
            "behavior_keep_action_effect_concise",
            text,
            ["simplify_repeated_context", "remove_redundant_connector"],
        )
        text = re.sub(r"^Khi gặp khó khăn", "Khi khó khăn", text, flags=re.IGNORECASE)
        add(
            "behavior_natural_compact",
            text,
            ["simplify_repeated_context", "remove_redundant_connector"],
        )
    elif role == "false_belief" and "tu nhu" in key:
        text = re.sub(
            r"Bạn tự nhủ rằng có lẽ họ chỉ không giỏi",
            "Bạn tự nhủ rằng có lẽ họ chưa giỏi",
            source,
            flags=re.IGNORECASE,
        )
        add("false_belief_natural_adverb_order", text, ["repair_adverb_order"])
        text = re.sub(r"Bạn tự nhủ rằng có lẽ", "Bạn tự nhủ có lẽ", text, flags=re.IGNORECASE)
        add(
            "false_belief_natural_concise",
            text,
            ["repair_adverb_order", "remove_optional_complementizer"],
        )
        text = re.sub(
            r"Bạn tự nhủ có lẽ họ chưa giỏi",
            "Bạn nghĩ họ chỉ chưa giỏi",
            text,
            flags=re.IGNORECASE,
        )
        add(
            "false_belief_spoken_compact",
            text,
            ["repair_adverb_order", "use_spoken_self_explanation"],
        )
    elif role == "underlying_truth" and "quan tam" in key:
        text = re.sub(r"^Nhưng\s+", "", source, flags=re.IGNORECASE)
        text = re.sub(r"sự quan tâm thật sự", "sự quan tâm thật lòng", text, flags=re.IGNORECASE)
        text = re.sub(
            r"không chỉ tồn tại trong những lúc một người",
            "không chỉ có khi người ta",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(r"điều gì đó", "", text, flags=re.IGNORECASE)
        add("truth_keep_transactional_contrast", text, ["simplify_subordinate_clause"])
    elif role == "concrete_sign" and "bien mat" in key:
        text = re.sub(r"Dấu hiệu rõ nhất là", "Dấu hiệu rõ là", source, flags=re.IGNORECASE)
        text = re.sub(
            r"ngay khi vấn đề của họ đã được giải quyết",
            "khi vấn đề được giải quyết",
            text,
            flags=re.IGNORECASE,
        )
        add("sign_keep_behavior_timing", text, ["remove_repeated_context"])
    elif role == "underlying_truth_and_concrete_sign":
        parts = re.split(r"(?<=[.!?])\s+", source, maxsplit=1)
        if len(parts) == 2:
            truth = _best_role_rewrite(parts[0], "underlying_truth")
            sign = _best_role_rewrite(parts[1], "concrete_sign")
            truth = truth.rstrip(".!?")
            sign = re.sub(r"^Dấu hiệu rõ(?: nhất)?(?: là)?:?\s*", "", sign, flags=re.IGNORECASE)
            add(
                "merge_truth_with_observable_sign",
                f"{truth}; {sign[0].lower() + sign[1:]}",
                ["merge_underlying_truth_and_concrete_sign"],
            )
            if (
                "quan tam" in _ascii_key(source)
                and "bien mat" in _ascii_key(source)
                and "nhan lai" in _ascii_key(source)
            ):
                add(
                    "merge_truth_sign_concise",
                    "Quan tâm thật lòng không chỉ để nhận lại; "
                    "họ biến mất sau khi vấn đề được giải quyết.",
                    [
                        "merge_underlying_truth_and_concrete_sign",
                        "remove_duplicated_context",
                    ],
                )
                add(
                    "merge_truth_sign_compact",
                    "Quan tâm không chỉ để nhận lại; "
                    "họ biến mất sau khi vấn đề được giải quyết.",
                    [
                        "merge_underlying_truth_and_concrete_sign",
                        "remove_duplicated_context",
                        "remove_optional_intensifier",
                    ],
                )
    elif role == "consequence" and "loi dung" in key:
        text = re.sub(
            r"xem việc bị lợi dụng như một phần bình thường của mối quan hệ",
            "coi sự lợi dụng là bình thường",
            source,
            flags=re.IGNORECASE,
        )
        add("consequence_natural_normalization", text, ["replace_verbose_consequence"])
        text = re.sub(r"chấp nhận điều đó", "chấp nhận", text, flags=re.IGNORECASE)
        add(
            "consequence_natural_concise",
            text,
            ["replace_verbose_consequence", "remove_repeated_context"],
        )
    elif role == "mature_perspective" and "nhat quan" in key:
        text = re.sub(
            r"nhìn vào sự nhất quán trong hành động",
            "nhìn vào hành động nhất quán",
            source,
            flags=re.IGNORECASE,
        )
        text = re.sub(r"thay vì chỉ tin vào", "thay vì", text, flags=re.IGNORECASE)
        add("perspective_keep_action_consistency", text, ["simplify_evaluation_standard"])
        text = re.sub(
            r"thay vì vài khoảnh khắc gần gũi",
            "thay vì những lúc gần gũi",
            text,
            flags=re.IGNORECASE,
        )
        add(
            "perspective_natural_compact",
            text,
            ["simplify_evaluation_standard", "use_natural_spoken_contrast"],
        )
        text = re.sub(
            r"thay vì những lúc gần gũi",
            "không chỉ lúc gần gũi",
            text,
            flags=re.IGNORECASE,
        )
        add(
            "perspective_spoken_compact",
            text,
            ["simplify_evaluation_standard", "use_natural_spoken_contrast"],
        )
    elif role == "conclusion" and _has_needed_vs_not_needed_contrast(source):
        add(
            "conclusion_keep_both_conditions",
            "Hãy so cách họ đối xử khi cần bạn và khi không cần gì để biết vị trí.",
            ["preserve_two_sided_contrast"],
        )
        add(
            "conclusion_compact_comparison",
            "Cách họ đối xử khi cần bạn và khi không cần gì cho thấy vị trí.",
            ["preserve_two_sided_contrast", "use_direct_evaluation"],
        )
    return candidates


def _best_role_rewrite(source: str, role: str) -> str:
    candidates = _fitting_candidates(source, role)
    safe = []
    for candidate_id, text, _ in candidates:
        fidelity = _evaluate_semantic_fidelity(source, text, role)
        if not fidelity["missing_anchors"] and not fidelity["missing_relations"]:
            if not _vietnamese_naturalness_errors(text):
                safe.append((_word_count(text), candidate_id, text))
    return min(safe)[2] if safe else source


def _apply_final_surface_realization(
    scenes: list[Scene],
    roles: tuple[str, ...],
) -> None:
    for scene, role in zip(scenes, roles):
        scene.final_surface_repair_applied = False
        scene.final_surface_candidate_id = scene.selected_fitting_candidate_id or "original"
        selected_surface = _evaluate_surface_quality(scene.voice_script, role)
        if selected_surface["status"] == "passed":
            continue

        candidates = _surface_repair_candidates(scene.voice_script, role)
        safe: list[tuple[int, str, str]] = []
        for candidate_id, candidate in candidates:
            evidence = _evaluate_evidence_fidelity(
                scene.original_voice_script,
                candidate,
                role,
            )
            fidelity = _evaluate_semantic_fidelity(
                scene.original_voice_script,
                candidate,
                role,
            )
            surface = _evaluate_surface_quality(candidate, role)
            ratio = max(
                0.0,
                (_word_count(scene.original_voice_script) - _word_count(candidate))
                / max(1, _word_count(scene.original_voice_script)),
            )
            merged_limit_exception = role == "underlying_truth_and_concrete_sign"
            if (
                not evidence["evidence_condition_complete"]
                or evidence["unsupported_inference_detected"]
                or fidelity["missing_anchors"]
                or fidelity["missing_relations"]
                or _vietnamese_naturalness_errors(candidate)
                or surface["status"] != "passed"
                or (ratio > _MAX_SCENE_COMPRESSION_RATIO and not merged_limit_exception)
            ):
                continue
            safe.append((_word_count(candidate), candidate_id, candidate))
        if not safe:
            continue
        _, candidate_id, candidate = min(safe, key=lambda item: (item[0], item[1]))
        scene.voice_script = candidate
        scene.final_surface_repair_applied = True
        scene.final_surface_candidate_id = candidate_id
        scene.narration_fit_applied = candidate != scene.original_voice_script
        scene.narration_fit_operations = list(
            dict.fromkeys([*scene.narration_fit_operations, "final_surface_repair"])
        )


def _surface_repair_candidates(text: str, role: str) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    key = _ascii_key(text)
    if role == "underlying_truth_and_concrete_sign" and (
        "quan tam" in key and "bien mat" in key
    ):
        clauses = [part.strip(" .") for part in text.split(";", maxsplit=1)]
        if len(clauses) == 2:
            evidence = re.sub(
                r"^họ\s+",
                "họ lại ",
                clauses[1],
                flags=re.IGNORECASE,
            )
            candidates.append(
                (
                    "surface_connected_truth_evidence",
                    _clean_sentence(
                        "Quan tâm thật lòng không chỉ xuất hiện khi cần nhận lại; "
                        f"{evidence}"
                    ),
                )
            )
    if role == "conclusion":
        prefix = re.sub(
            r"\s+(?:cho thấy|thể hiện|nói lên|phản ánh)\s+"
            r"(?:vị trí|vai trò|giá trị|câu trả lời)\.?$",
            "",
            text,
            flags=re.IGNORECASE,
        ).strip()
        if prefix and prefix != text:
            candidates.append(
                (
                    "surface_complete_conclusion",
                    _clean_sentence(
                        f"{prefix} là câu trả lời"
                    ),
                )
            )
    return candidates


def _merge_truth_and_evidence_scenes(
    scenes: list[Scene],
) -> tuple[list[Scene], tuple[str, ...]]:
    truth = scenes[3]
    sign = scenes[4]
    merged = truth.model_copy(deep=True)
    merged.original_voice_script = (
        f"{truth.original_voice_script} {sign.original_voice_script}"
    )
    merged.voice_script = merged.original_voice_script
    merged.original_narration_word_count = (
        truth.original_narration_word_count + sign.original_narration_word_count
    )
    merged.original_estimated_duration_seconds = round(
        truth.original_estimated_duration_seconds
        + sign.original_estimated_duration_seconds,
        2,
    )
    merged.title = "Underlying Truth And Concrete Sign"
    merged.scene_meaning = merged.original_voice_script
    merged.narration_fit_applied = False
    merged.narration_fit_operations = []
    merged.selected_fitting_candidate_id = ""
    result = [*scenes[:3], merged, *scenes[5:]]
    for index, scene in enumerate(result, start=1):
        scene.scene_index = index
    return result, _ROLES_7


def _fit_errors(
    plan: TellaScenePlan,
    scenes: list[Scene],
    roles: tuple[str, ...],
) -> list[str]:
    errors: list[str] = []
    fitted_seconds = plan.fitted_estimated_duration_seconds
    if not (_HARD_DURATION_RANGE[0] <= fitted_seconds <= _HARD_DURATION_RANGE[1]):
        errors.append(f"fitted duration {fitted_seconds:.2f}s is outside 32-38s")
    if _has_overlong_scene(scenes, roles, plan.voice_edge_rate):
        errors.append("one or more fitted scenes remain above the role-aware range")
    if any(scene.semantic_anchor_loss_detected for scene in scenes):
        errors.append("semantic anchor loss detected after compression")
    if any(scene.semantic_relation_loss_detected for scene in scenes):
        errors.append("semantic relation loss detected after compression")
    if any(not scene.evidence_condition_complete for scene in scenes):
        errors.append("observable evidence condition is incomplete")
    if any(scene.unsupported_inference_detected for scene in scenes):
        errors.append("unsupported private-motive inference detected")
    if any(scene.vietnamese_naturalness_status == "failed" for scene in scenes):
        errors.append("Vietnamese naturalness validation failed")
    if any(scene.surface_quality_status == "failed" for scene in scenes):
        errors.append("Vietnamese surface-quality validation failed")
    unsafe_ratios = [
        str(scene.scene_index)
        for scene, role in zip(scenes, roles)
        if scene.compression_limit_exceeded
        and role != "underlying_truth_and_concrete_sign"
    ]
    if unsafe_ratios:
        errors.append(
            "scene compression limit exceeded without semantic merge: "
            + ", ".join(unsafe_ratios)
        )
    return errors


def _evaluate_evidence_fidelity(
    source: str,
    candidate: str,
    role: str,
) -> dict[str, Any]:
    source_key = _ascii_key(source)
    candidate_key = _ascii_key(candidate)

    def has_help_condition(key: str) -> bool:
        return any(
            term in key
            for term in ("can nhan lai", "can giup", "can ban", "can dieu gi")
        )

    def has_resolution_event(key: str) -> bool:
        return bool(
            re.search(r"\bvan de\b.{0,35}\b(?:duoc )?giai quyet\b", key)
            or "kho khan qua di" in key
        )

    def has_disappearance_after_resolution(key: str) -> bool:
        return bool(
            re.search(
                r"\bbien mat\b.{0,20}\b(?:ngay )?(?:sau khi|khi)\b"
                r".{0,35}\bvan de\b.{0,35}\b(?:duoc )?giai quyet\b",
                key,
            )
            or re.search(
                r"\b(?:sau khi|khi)\b.{0,35}\bvan de\b.{0,35}"
                r"\b(?:duoc )?giai quyet\b.{0,20}\bbien mat\b",
                key,
            )
        )

    motive_terms = {
        "selfish_motive": ("ich ky",),
        "transactional_motive": ("vu loi",),
        "fake_care": ("gia vo quan tam", "quan tam gia tao", "khong that long"),
        "deliberate_exploitation": (
            "co tinh loi dung",
            "chi loi dung ban",
            "loi dung ban de",
        ),
    }
    source_motives = {
        name
        for name, terms in motive_terms.items()
        if any(term in source_key for term in terms)
    }
    candidate_motives = {
        name
        for name, terms in motive_terms.items()
        if any(term in candidate_key for term in terms)
    }
    unsupported_motives = sorted(candidate_motives - source_motives)

    source_help = has_help_condition(source_key)
    source_resolution = has_resolution_event(source_key)
    source_disappearance = "bien mat" in source_key
    source_after_resolution = has_disappearance_after_resolution(source_key)
    candidate_help = has_help_condition(candidate_key)
    candidate_resolution = has_resolution_event(candidate_key)
    candidate_disappearance = "bien mat" in candidate_key
    candidate_after_resolution = has_disappearance_after_resolution(candidate_key)
    candidate_contrast = (
        "quan tam" in candidate_key
        and candidate_help
        and candidate_disappearance
        and any(
            term in candidate_key
            for term in ("nhung", "lai bien mat", "the nhung", "trong khi")
        )
        or (
            "quan tam" in candidate_key
            and candidate_help
            and candidate_disappearance
            and ";" in candidate
        )
    )

    required_relations: list[str] = []
    preserved_relations: list[str] = []
    if role == "underlying_truth_and_concrete_sign":
        relation_checks = (
            ("help_needed_condition", source_help, candidate_help),
            (
                "problem_resolution_event",
                source_resolution,
                candidate_resolution,
            ),
            (
                "disappearance_after_resolution",
                source_after_resolution or (source_resolution and source_disappearance),
                candidate_after_resolution,
            ),
            (
                "contrast_between_genuine_care_and_observed_pattern",
                "quan tam" in source_key and source_help and source_disappearance,
                candidate_contrast,
            ),
        )
        for name, required, preserved in relation_checks:
            if required:
                required_relations.append(name)
                if preserved:
                    preserved_relations.append(name)

    observable_claims = []
    if candidate_help:
        observable_claims.append("help_or_return_needed_condition")
    if candidate_resolution:
        observable_claims.append("problem_resolution_event")
    if candidate_disappearance:
        observable_claims.append("disappearance")
    if candidate_after_resolution:
        observable_claims.append("disappearance_after_resolution")

    evidence_condition_complete = not (
        role == "underlying_truth_and_concrete_sign"
        and any(
            relation not in preserved_relations
            for relation in required_relations
            if relation
            in {
                "help_needed_condition",
                "problem_resolution_event",
                "disappearance_after_resolution",
            }
        )
    )
    return {
        "observable_claims": observable_claims,
        "inferred_private_motives": sorted(candidate_motives),
        "unsupported_inference_detected": bool(unsupported_motives),
        "unsupported_inference_reasons": [
            f"private motive not supported by source: {name}"
            for name in unsupported_motives
        ],
        "evidence_condition_complete": evidence_condition_complete,
        "required_relations": required_relations,
        "preserved_relations": preserved_relations,
        "missing_relations": [
            name for name in required_relations if name not in preserved_relations
        ],
    }


def _evaluate_semantic_fidelity(
    source: str,
    candidate: str,
    role: str,
) -> dict[str, list[str]]:
    source_anchors = _semantic_anchor_checks(source, role)
    candidate_anchors = _semantic_anchor_checks(candidate, role)
    required_anchors = [name for name, present in source_anchors.items() if present]
    preserved_anchors = [
        name for name in required_anchors if candidate_anchors.get(name, False)
    ]
    source_relations = _semantic_relation_checks(source, role)
    candidate_relations = _semantic_relation_checks(candidate, role)
    required_relations = [name for name, present in source_relations.items() if present]
    preserved_relations = [
        name for name in required_relations if candidate_relations.get(name, False)
    ]
    evidence = _evaluate_evidence_fidelity(source, candidate, role)
    required_relations = list(
        dict.fromkeys([*required_relations, *evidence["required_relations"]])
    )
    preserved_relations = list(
        dict.fromkeys([*preserved_relations, *evidence["preserved_relations"]])
    )
    return {
        "required_anchors": required_anchors,
        "preserved_anchors": preserved_anchors,
        "missing_anchors": [
            name for name in required_anchors if name not in preserved_anchors
        ],
        "required_relations": required_relations,
        "preserved_relations": preserved_relations,
        "missing_relations": [
            name for name in required_relations if name not in preserved_relations
        ],
    }


def _semantic_anchor_checks(text: str, role: str) -> dict[str, bool]:
    key = _ascii_key(text)
    has_need = any(term in key for term in ("can ban", "can giup", "can nhan lai"))
    has_not_need = any(
        term in key for term in ("khong can gi", "khong can ban", "khong con co don")
    )
    checks: dict[str, dict[str, bool]] = {
        "hook": {
            "pattern_subject": any(term in key for term in ("kieu quan tam", "mau hanh vi", "vi sao")),
            "trigger_condition": "khi" in key and has_need,
        },
        "behavior": {
            "observable_actor": any(term in key for term in ("ho", "nguoi ta", "ban")),
            "observable_action": any(term in key for term in ("tim den", "tra loi", "phan ung", "lien lac")),
            "resulting_effect": any(term in key for term in ("khien ban", "thay minh", "hy vong")),
        },
        "false_belief": {
            "self_explanation_marker": any(term in key for term in ("tu nhu", "ban nghi", "giai thich", "tin rang")),
            "excusing_belief": any(term in key for term in ("co le", "khong gioi", "chua gioi", "khoanh khac")),
        },
        "underlying_truth": {
            "genuine_care_standard": any(term in key for term in ("quan tam", "su that", "nhat quan")),
            "transactional_contrast": any(term in key for term in ("khong chi", "nhan lai", "loi ich", "vu loi")),
        },
        "concrete_sign": {
            "observable_behavior": any(term in key for term in ("bien mat", "hanh dong", "xuat hien")),
            "behavior_timing": any(term in key for term in ("khi van de", "sau khi", "hoan canh doi")),
        },
        "underlying_truth_and_concrete_sign": {
            "genuine_care_standard": any(term in key for term in ("quan tam", "su that", "nhat quan")),
            "transactional_contrast": any(term in key for term in ("khong chi", "nhan lai", "loi ich", "vu loi")),
            "observable_behavior": any(term in key for term in ("bien mat", "hanh dong", "xuat hien")),
            "behavior_timing": any(term in key for term in ("khi van de", "sau khi", "hoan canh doi")),
        },
        "consequence": {
            "continued_acceptance": any(term in key for term in ("tiep tuc", "chap nhan", "bo qua")),
            "harmful_treatment": any(term in key for term in ("loi dung", "ky vong thieu can cu")),
            "normalization_effect": any(term in key for term in ("binh thuong", "dan xem", "dau tu")),
        },
        "mature_perspective": {
            "evaluation_standard": any(term in key for term in ("truong thanh", "hay nhin", "quan sat")),
            "consistency": "nhat quan" in key,
            "behavior_or_action": any(term in key for term in ("hanh dong", "hanh vi")),
            "isolated_moments": any(term in key for term in ("khoanh khac", "nhung luc", "luc gan gui")),
        },
        "conclusion": {
            "first_comparison_condition": has_need,
            "second_comparison_condition": has_not_need,
            "evaluation_instruction": any(term in key for term in ("hay", "dung", "danh gia", "cho thay", "de biet", "cau tra loi")),
            "final_standard": any(term in key for term in ("vi tri", "tran trong", "hien dien", "cau tra loi")),
        },
    }
    return checks.get(role, {})


def _semantic_relation_checks(text: str, role: str) -> dict[str, bool]:
    key = _ascii_key(text)
    anchors = _semantic_anchor_checks(text, role)
    contrast_marker = any(term in key for term in ("khong chi", "thay vi", "khong chi", "va khi")) or ";" in text
    relations: dict[str, dict[str, bool]] = {
        "hook": {
            "pattern_triggered_by_need": anchors.get("pattern_subject", False)
            and anchors.get("trigger_condition", False),
        },
        "behavior": {
            "action_causes_resulting_effect": anchors.get("observable_actor", False)
            and anchors.get("observable_action", False)
            and anchors.get("resulting_effect", False),
        },
        "false_belief": {
            "self_explanation_excuses_behavior": anchors.get("self_explanation_marker", False)
            and anchors.get("excusing_belief", False),
        },
        "underlying_truth": {
            "genuine_care_contrasted_with_transaction": anchors.get("genuine_care_standard", False)
            and anchors.get("transactional_contrast", False),
        },
        "concrete_sign": {
            "behavior_occurs_after_condition": anchors.get("observable_behavior", False)
            and anchors.get("behavior_timing", False),
        },
        "underlying_truth_and_concrete_sign": {
            "genuine_care_contrasted_with_transaction": anchors.get("genuine_care_standard", False)
            and anchors.get("transactional_contrast", False),
            "behavior_occurs_after_condition": anchors.get("observable_behavior", False)
            and anchors.get("behavior_timing", False),
        },
        "consequence": {
            "continued_acceptance_normalizes_harm": anchors.get("continued_acceptance", False)
            and anchors.get("harmful_treatment", False)
            and anchors.get("normalization_effect", False),
        },
        "mature_perspective": {
            "consistent_actions_over_isolated_moments": anchors.get("consistency", False)
            and anchors.get("behavior_or_action", False)
            and anchors.get("isolated_moments", False)
            and contrast_marker,
        },
        "conclusion": {
            "needed_vs_not_needed_contrast": anchors.get("first_comparison_condition", False)
            and anchors.get("second_comparison_condition", False)
            and (" va khi " in f" {key} " or ";" in text),
            "comparison_guides_final_evaluation": anchors.get("evaluation_instruction", False)
            and anchors.get("final_standard", False),
        },
    }
    return relations.get(role, {})


def _has_needed_vs_not_needed_contrast(text: str) -> bool:
    checks = _semantic_relation_checks(text, "conclusion")
    return checks.get("needed_vs_not_needed_contrast", False)


def _evaluate_surface_quality(text: str, role: str) -> dict[str, Any]:
    key = _ascii_key(text)
    reasons: list[str] = []
    unresolved_nouns = ("vi tri", "vai tro", "gia tri", "cau tra loi")
    complement_verbs = ("cho thay", "the hien", "noi len", "phan anh", "de biet")
    unresolved_predicate = any(
        re.search(rf"\b{verb} {noun}$", key)
        for verb in complement_verbs
        for noun in unresolved_nouns
    )
    incomplete_transaction = bool(
        re.search(r"\bquan tam(?: that long)? khong chi de nhan lai\b", key)
    )
    missing_preposition = "nhin su nhat quan" in key and (
        "hay nhin su nhat quan" not in key
    )
    malformed_exploitation = any(
        term in key for term in ("xem bi loi dung", "coi bi loi dung")
    )

    predicate_complete = not (
        unresolved_predicate or incomplete_transaction or _word_count(text) < 6
    )
    complement_complete = not (
        unresolved_predicate or missing_preposition or malformed_exploitation
    )
    if unresolved_predicate:
        reasons.append("predicate ends with an unresolved abstract complement")
    if incomplete_transaction:
        reasons.append("transactional-care predicate is incomplete")
    if missing_preposition:
        reasons.append("required Vietnamese preposition is missing")
    if malformed_exploitation:
        reasons.append("exploitation predicate lacks a grammatical object")

    clauses = [part.strip() for part in re.split(r"[;.!?]+", text) if part.strip()]
    clause_connection_natural = True
    if role == "underlying_truth_and_concrete_sign":
        connection_terms = (
            "cho thay",
            "dau hieu",
            "vi vay",
            "nguoc lai",
            "qua do",
            "lai bien mat",
        )
        clause_connection_natural = (
            len(clauses) >= 2
            and any(term in _ascii_key(clauses[-1]) for term in connection_terms)
        )
        if not clause_connection_natural:
            reasons.append("truth and evidence clauses are not naturally connected")
    elif len(clauses) >= 2 and ";" in text:
        clause_connection_natural = any(
            term in key for term in ("hay", "nhung", "vi vay", "cho thay", "nguoc lai")
        )
        if not clause_connection_natural:
            reasons.append("adjacent clauses lack a natural connector")

    pronoun_reference_clear = not bool(
        re.search(r"\b(?:nguoi nay|nguoi kia|ho do)\b", key)
    )
    if not pronoun_reference_clear:
        reasons.append("pronoun reference is ambiguous")
    if any(_word_count(clause) <= 2 for clause in clauses):
        reasons.append("keyword-fragment clause detected")
    if text and text[-1] not in ".!?":
        reasons.append("surface realization lacks terminal punctuation")

    reasons = list(dict.fromkeys(reasons))
    return {
        "status": "passed" if not reasons else "failed",
        "failure_reasons": reasons,
        "predicate_complete": predicate_complete,
        "complement_complete": complement_complete,
        "clause_connection_natural": clause_connection_natural,
        "pronoun_reference_clear": pronoun_reference_clear,
    }


def _vietnamese_naturalness_errors(text: str) -> list[str]:
    key = _ascii_key(text)
    errors: list[str] = []
    if any(term in key for term in ("xem bi loi dung", "coi bi loi dung")):
        errors.append("unnatural exploitation construction")
    if "ho co le" in key:
        errors.append("unnatural adverb order; use 'có lẽ họ'")
    if "nhin su nhat quan" in key and "hay nhin su nhat quan" not in key:
        errors.append("missing preposition before 'sự nhất quán'")
    if _ends_with_dangling_conjunction(text):
        errors.append("sentence ends with a dangling conjunction")
    if text and text[-1] not in ".!?":
        errors.append("sentence lacks terminal punctuation")
    words = _word_count(text)
    if words < 6:
        errors.append("telegraphic fragment")
    clauses = [
        part for part in re.split(r"[.!?;]+", text) if part.strip()
    ]
    if len(clauses) >= 2 and all(_word_count(clause) <= 3 for clause in clauses):
        errors.append("repeated telegraphic fragments")
    return errors


def _clean_sentence(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,.;!?])", r"\1", text)
    if text:
        text = text[0].upper() + text[1:]
    if text and text[-1] not in ".!?":
        text += "."
    return text


def _maximum_scene_duration(role: str) -> float:
    return {
        "underlying_truth_and_concrete_sign": 7.8,
        "consequence": 5.8,
        "mature_perspective": 5.8,
        "conclusion": 6.7,
    }.get(role, 5.2)


def _estimated_total(
    scenes: list[Scene],
    roles: tuple[str, ...],
    voice_rate: str,
) -> float:
    return round(
        sum(
            _estimate_scene_duration(_word_count(scene.voice_script), index, role, voice_rate)
            for index, (scene, role) in enumerate(zip(scenes, roles))
        ),
        2,
    )


def _has_overlong_scene(
    scenes: list[Scene],
    roles: tuple[str, ...],
    voice_rate: str,
) -> bool:
    return any(
        _estimate_scene_duration(_word_count(scene.voice_script), index, role, voice_rate)
        > _maximum_scene_duration(role)
        for index, (scene, role) in enumerate(zip(scenes, roles))
    )


def _estimate_scene_duration(
    word_count: int,
    index: int,
    role: str,
    voice_rate: str,
) -> float:
    duration = word_count / _words_per_second(voice_rate)
    if index > 0:
        duration += 0.35
    if role == "conclusion":
        duration += 0.15
    return round(duration, 2)


def _words_per_second(voice_rate: str) -> float:
    percent = int(normalize_voice_rate(voice_rate).rstrip("%"))
    # 3.0 words/second is calibrated to the recipe's firm_male_vi rate (-5%).
    return max(1.5, 3.0 * (100 + percent) / 95.0)


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[^\W_]+\b", text, flags=re.UNICODE))


def _ends_with_dangling_conjunction(text: str) -> bool:
    words = re.findall(r"\b[^\W_]+\b", _ascii_key(text), flags=re.UNICODE)
    return bool(words and words[-1] in _DANGLING_CONJUNCTIONS)


def _script_segments(script: str) -> list[str]:
    raw = (script or "").strip()
    if not raw:
        raise ValueError("life insight script is empty")
    lines = [re.sub(r"\s+", " ", line).strip() for line in raw.splitlines() if line.strip()]
    if len(lines) == 1:
        lines = [
            item.strip()
            for item in re.split(r"(?<=[.!?…])\s+", lines[0])
            if item.strip()
        ]
    return lines


def _language_quality_errors(scenes: list[Scene]) -> list[str]:
    text = " ".join(scene.voice_script for scene in scenes)
    key = _ascii_key(text)
    errors = []
    for filler in _FILLERS:
        if key.count(filler) > 1:
            errors.append(f"Vietnamese filler phrase repeated excessively: {filler}")
    if sum(scene.voice_script.count("?") for scene in scenes) > 1:
        errors.append("life insight narration uses excessive rhetorical questions")
    for term in _PRIVATE_THOUGHT_PATTERNS:
        if term in key:
            errors.append(
                f"narration claims private thoughts instead of observation: {term}"
            )
    for term in _CRUEL_OR_DIAGNOSTIC_TERMS:
        if term in key:
            errors.append(f"narration is cruel or diagnostic: {term}")
    return errors


def _highlight_words(text: str) -> list[str]:
    words = re.findall(r"\b[^\W_]+\b", text, flags=re.UNICODE)
    return words[-2:] if len(words) >= 2 else words


def _ascii_key(text: str) -> str:
    import unicodedata

    normalized = unicodedata.normalize("NFKD", (text or "").lower())
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = normalized.replace("đ", "d")
    return re.sub(r"[^a-z0-9]+", " ", normalized).strip()


__all__ = [
    "enforce_life_insight_plan",
    "plan_life_insight_from_script",
    "plan_life_insight_from_topic",
]
