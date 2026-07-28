import type { PlanOnlyCreateRequestV1 } from "../contracts/v1/production";

export const CONTENT_MIN_CHARACTERS = 1;
export const CONTENT_MAX_CHARACTERS = 5000;
export const FIXED_TARGET_DURATION_SECONDS = 35;
export const FIXED_ASPECT_RATIO = "9:16";

export const creationStepLabels = [
  "Content",
  "Production profile",
  "Character scope",
  "Review",
  "Create StoryPlan",
] as const;

export type CreationStep = 1 | 2 | 3 | 4 | 5;
export type DraftField =
  | "inputMode"
  | "sourceContent"
  | "language"
  | "requestedSceneCount"
  | "characterScope";

export interface ProductionCreationDraft {
  readonly inputMode: "TOPIC";
  readonly sourceContent: string;
  readonly language: "en" | "vi";
  readonly requestedSceneCount: 7 | 8;
  readonly characterScope:
    | "recurring_female"
    | "female_with_anonymous_background";
  readonly productionMode: "PLAN_ONLY";
}

export interface DraftValidationIssue {
  readonly field: DraftField;
  readonly message: string;
}

export const initialProductionCreationDraft: ProductionCreationDraft = Object.freeze({
  inputMode: "TOPIC",
  sourceContent: "",
  language: "en",
  requestedSceneCount: 8,
  characterScope: "recurring_female",
  productionMode: "PLAN_ONLY",
});

export function contentCharacterCount(value: string): number {
  return Array.from(value).length;
}

export function validateCreationDraft(
  draft: ProductionCreationDraft,
): readonly DraftValidationIssue[] {
  const issues: DraftValidationIssue[] = [];
  const contentLength = contentCharacterCount(draft.sourceContent);
  if (draft.inputMode !== "TOPIC") {
    issues.push({
      field: "inputMode",
      message: "Only the authorized Topic input mode is available.",
    });
  }
  if (draft.sourceContent.trim().length === 0) {
    issues.push({
      field: "sourceContent",
      message: "Enter a topic before continuing.",
    });
  } else if (contentLength > CONTENT_MAX_CHARACTERS) {
    issues.push({
      field: "sourceContent",
      message: `Keep the topic at ${CONTENT_MAX_CHARACTERS} characters or fewer.`,
    });
  }
  if (draft.language !== "en" && draft.language !== "vi") {
    issues.push({
      field: "language",
      message: "Select a supported language.",
    });
  }
  if (draft.requestedSceneCount !== 7 && draft.requestedSceneCount !== 8) {
    issues.push({
      field: "requestedSceneCount",
      message: "Select seven or eight scenes.",
    });
  }
  if (
    draft.characterScope !== "recurring_female" &&
    draft.characterScope !== "female_with_anonymous_background"
  ) {
    issues.push({
      field: "characterScope",
      message: "Select a supported female-safe character scope.",
    });
  }
  return issues;
}

export function validateCreationStep(
  draft: ProductionCreationDraft,
  step: CreationStep,
): readonly DraftValidationIssue[] {
  const stepFields: Readonly<Record<CreationStep, readonly DraftField[]>> = {
    1: ["inputMode", "sourceContent"],
    2: ["language", "requestedSceneCount"],
    3: ["characterScope"],
    4: [],
    5: [],
  };
  return validateCreationDraft(draft).filter((issue) =>
    stepFields[step].includes(issue.field),
  );
}

export function mapDraftToPlanOnlyRequest(
  draft: ProductionCreationDraft,
): PlanOnlyCreateRequestV1 {
  const issues = validateCreationDraft(draft);
  if (issues.length > 0) {
    throw new Error("Cannot map an invalid production creation draft.");
  }
  return {
    schema_version: 1,
    input_mode: draft.inputMode,
    source_content: draft.sourceContent,
    language: draft.language,
    character_scope: draft.characterScope,
    requested_scene_count: draft.requestedSceneCount,
  };
}

export function isMeaningfulCreationDraft(draft: ProductionCreationDraft): boolean {
  return (
    draft.sourceContent.length > 0 ||
    draft.language !== initialProductionCreationDraft.language ||
    draft.requestedSceneCount !== initialProductionCreationDraft.requestedSceneCount ||
    draft.characterScope !== initialProductionCreationDraft.characterScope
  );
}

export function inputModeLabel(value: ProductionCreationDraft["inputMode"]): string {
  return value === "TOPIC" ? "Topic" : value;
}

export function languageLabel(value: ProductionCreationDraft["language"]): string {
  return value === "vi" ? "Vietnamese" : "English";
}

export function characterScopeLabel(
  value: ProductionCreationDraft["characterScope"],
): string {
  return value === "female_with_anonymous_background"
    ? "Recurring woman with anonymous background people"
    : "One recurring woman";
}
