import type { ReactNode } from "react";

import type {
  CreationStep,
  DraftField,
  ProductionCreationDraft,
} from "../../productionCreation/creationDraft";
import {
  characterScopeLabel,
  CONTENT_MAX_CHARACTERS,
  CONTENT_MIN_CHARACTERS,
  contentCharacterCount,
  creationStepLabels,
  FIXED_ASPECT_RATIO,
  FIXED_TARGET_DURATION_SECONDS,
  inputModeLabel,
  languageLabel,
} from "../../productionCreation/creationDraft";

export type DraftErrors = Readonly<Partial<Record<DraftField, string>>>;

interface DraftStepProps {
  readonly draft: ProductionCreationDraft;
  readonly errors: DraftErrors;
  readonly updateDraft: (patch: Partial<ProductionCreationDraft>) => void;
}

function InlineError({
  id,
  message,
}: {
  readonly id: string;
  readonly message: string | undefined;
}) {
  return message ? (
    <p className="field-error" id={id}>
      {message}
    </p>
  ) : null;
}

export function CreationStepNavigation({
  currentStep,
  maxReachableStep,
  onSelect,
}: {
  readonly currentStep: CreationStep;
  readonly maxReachableStep: CreationStep;
  readonly onSelect: (step: CreationStep) => void;
}) {
  return (
    <nav className="creation-steps" aria-label="StoryPlan creation steps">
      <ol>
        {creationStepLabels.map((label, index) => {
          const step = (index + 1) as CreationStep;
          const current = step === currentStep;
          const completed = step < maxReachableStep;
          const reachable = step <= maxReachableStep && step < 5;
          return (
            <li
              className={[
                current ? "creation-step--current" : "",
                completed ? "creation-step--completed" : "",
              ]
                .filter(Boolean)
                .join(" ")}
              key={label}
            >
              {reachable && !current ? (
                <button type="button" onClick={() => onSelect(step)}>
                  <span>{step}</span>
                  <span>
                    {label}
                    {completed ? <small>Completed</small> : null}
                  </span>
                </button>
              ) : (
                <div aria-current={current ? "step" : undefined}>
                  <span>{step}</span>
                  <span>{label}</span>
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

export function ContentStep({ draft, errors, updateDraft }: DraftStepProps) {
  const contentError = errors.sourceContent;
  return (
    <fieldset className="surface creation-step-panel">
      <legend>Content</legend>
      <p className="supporting-copy">
        Choose an authorized input mode and provide the content exactly as it should be
        submitted.
      </p>

      <fieldset className="nested-fieldset">
        <legend>Input mode</legend>
        <label className="choice-card">
          <input type="radio" name="input-mode" value="TOPIC" checked readOnly />
          <span>
            <strong>Topic</strong>
            <small>
              Plan a StoryPlan from one topic. Full-script and outline segmentation are not
              authorized.
            </small>
          </span>
        </label>
      </fieldset>

      <div className="field-stack">
        <label htmlFor="source-content">Topic content</label>
        <textarea
          id="source-content"
          name="source-content"
          rows={9}
          value={draft.sourceContent}
          aria-invalid={contentError ? "true" : undefined}
          aria-describedby={
            contentError ? "source-content-help source-content-error" : "source-content-help"
          }
          onChange={(event) => updateDraft({ sourceContent: event.currentTarget.value })}
        />
        <div className="field-meta" id="source-content-help">
          <span>
            {CONTENT_MIN_CHARACTERS}–{CONTENT_MAX_CHARACTERS} characters. Whitespace and
            paragraphs are submitted unchanged.
          </span>
          <span>{contentCharacterCount(draft.sourceContent)} characters</span>
        </div>
        <InlineError id="source-content-error" message={contentError} />
      </div>
    </fieldset>
  );
}

export function ProductionProfileStep({
  draft,
  errors,
  updateDraft,
}: DraftStepProps) {
  return (
    <fieldset className="surface creation-step-panel">
      <legend>Production profile</legend>
      <p className="supporting-copy">
        Only values consumed by the current PLAN_ONLY contract can be changed.
      </p>

      <div className="field-grid">
        <div>
          <label htmlFor="production-language">Language</label>
          <select
            id="production-language"
            value={draft.language}
            aria-invalid={errors.language ? "true" : undefined}
            aria-describedby={errors.language ? "production-language-error" : undefined}
            onChange={(event) =>
              updateDraft({ language: event.currentTarget.value === "vi" ? "vi" : "en" })
            }
          >
            <option value="en">English</option>
            <option value="vi">Vietnamese</option>
          </select>
          <InlineError id="production-language-error" message={errors.language} />
        </div>
        <div>
          <label htmlFor="scene-count">StoryPlan scenes</label>
          <select
            id="scene-count"
            value={draft.requestedSceneCount}
            aria-invalid={errors.requestedSceneCount ? "true" : undefined}
            aria-describedby={
              errors.requestedSceneCount
                ? "scene-count-help scene-count-error"
                : "scene-count-help"
            }
            onChange={(event) =>
              updateDraft({
                requestedSceneCount: event.currentTarget.value === "7" ? 7 : 8,
              })
            }
          >
            <option value="7">7 scenes</option>
            <option value="8">8 scenes</option>
          </select>
          <p className="field-help" id="scene-count-help">
            The backend currently accepts exactly seven or eight planned scenes.
          </p>
          <InlineError id="scene-count-error" message={errors.requestedSceneCount} />
        </div>
      </div>

      <dl className="profile-facts">
        <div>
          <dt>Target duration</dt>
          <dd>{FIXED_TARGET_DURATION_SECONDS} seconds — planned target</dd>
        </div>
        <div>
          <dt>Aspect ratio</dt>
          <dd>{FIXED_ASPECT_RATIO} — portrait</dd>
        </div>
        <div>
          <dt>Pace</dt>
          <dd>Not available — omitted from the request</dd>
        </div>
        <div>
          <dt>Narration preference</dt>
          <dd>Not available — omitted from the request</dd>
        </div>
      </dl>

      <fieldset className="nested-fieldset mode-grid">
        <legend>Production mode</legend>
        <label className="mode-option">
          <input
            type="radio"
            name="execution-mode"
            value="PLAN_ONLY"
            aria-describedby="plan-only-mode-help"
            checked
            readOnly
          />
          <span>
            <strong>PLAN_ONLY</strong>
            <small id="plan-only-mode-help">
              Creates a StoryPlan only. No narration, media, or video is generated.
            </small>
          </span>
        </label>
        <label className="mode-option mode-option--disabled">
          <input
            type="radio"
            name="execution-mode"
            value="FULL_RENDER"
            aria-describedby="full-render-mode-help"
            disabled
          />
          <span>
            <strong>FULL_RENDER</strong>
            <small id="full-render-mode-help">
              Disabled because this workflow has no render authority.
            </small>
          </span>
        </label>
      </fieldset>
    </fieldset>
  );
}

export function CharacterScopeStep({
  draft,
  errors,
  updateDraft,
}: DraftStepProps) {
  return (
    <fieldset
      className="surface creation-step-panel"
      aria-describedby={errors.characterScope ? "character-scope-error" : undefined}
    >
      <legend>Character scope</legend>
      <p className="supporting-copy">
        Select an explicit female-safe scope. Topic text is never used to infer this value.
      </p>
      <div className="choice-grid">
        <label className="choice-card">
          <input
            id="character-scope-recurring-female"
            type="radio"
            name="character-scope"
            value="recurring_female"
            checked={draft.characterScope === "recurring_female"}
            onChange={() => updateDraft({ characterScope: "recurring_female" })}
          />
          <span>
            <strong>One recurring woman</strong>
            <small>
              A single declared female identity may recur across the planned scenes.
            </small>
          </span>
        </label>
        <label className="choice-card">
          <input
            type="radio"
            name="character-scope"
            value="female_with_anonymous_background"
            checked={draft.characterScope === "female_with_anonymous_background"}
            onChange={() =>
              updateDraft({ characterScope: "female_with_anonymous_background" })
            }
          />
          <span>
            <strong>Recurring woman with anonymous background people</strong>
            <small>
              Keeps one recurring woman while allowing non-identifiable background figures.
            </small>
          </span>
        </label>
      </div>
      <InlineError id="character-scope-error" message={errors.characterScope} />
    </fieldset>
  );
}

function ReviewValue({
  label,
  children,
}: {
  readonly label: string;
  readonly children: ReactNode;
}) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

export function ReviewStep({
  draft,
  editStep,
}: {
  readonly draft: ProductionCreationDraft;
  readonly editStep: (step: CreationStep) => void;
}) {
  return (
    <section className="surface creation-step-panel" aria-labelledby="review-title">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Exact request review</p>
          <h2 id="review-title">Review StoryPlan request</h2>
        </div>
      </div>
      <div className="review-group">
        <div className="review-group__heading">
          <h3>Content</h3>
          <button type="button" onClick={() => editStep(1)}>
            Edit content
          </button>
        </div>
        <dl className="review-list">
          <ReviewValue label="Input mode">{inputModeLabel(draft.inputMode)}</ReviewValue>
          <ReviewValue label="Supplied content">
            <pre className="review-content">{draft.sourceContent}</pre>
          </ReviewValue>
        </dl>
      </div>

      <div className="review-group">
        <div className="review-group__heading">
          <h3>Production profile</h3>
          <button type="button" onClick={() => editStep(2)}>
            Edit production profile
          </button>
        </div>
        <dl className="review-list">
          <ReviewValue label="Language">{languageLabel(draft.language)}</ReviewValue>
          <ReviewValue label="StoryPlan scenes">{draft.requestedSceneCount}</ReviewValue>
          <ReviewValue label="Target duration">
            {FIXED_TARGET_DURATION_SECONDS} seconds — planned target
          </ReviewValue>
          <ReviewValue label="Pace">Not available — omitted</ReviewValue>
          <ReviewValue label="Aspect ratio">{FIXED_ASPECT_RATIO}</ReviewValue>
          <ReviewValue label="Narration preference">
            Not available — omitted
          </ReviewValue>
          <ReviewValue label="Production mode">{draft.productionMode}</ReviewValue>
        </dl>
      </div>

      <div className="review-group">
        <div className="review-group__heading">
          <h3>Character scope</h3>
          <button type="button" onClick={() => editStep(3)}>
            Edit character scope
          </button>
        </div>
        <dl className="review-list">
          <ReviewValue label="Character scope">
            {characterScopeLabel(draft.characterScope)}
          </ReviewValue>
        </dl>
      </div>

      <div className="storyplan-only-notice">
        <strong>This creates a StoryPlan only.</strong>
        <p>No narration, images, audio, renderer plan, media, or video will be generated.</p>
      </div>
    </section>
  );
}

export function CreatingStoryPlanStep() {
  return (
    <section className="surface creation-step-panel" aria-labelledby="creating-title">
      <p className="eyebrow">Step 5 of 5</p>
      <h2 id="creating-title">Creating StoryPlan</h2>
      <p role="status" aria-live="polite">
        Planning… The request cannot be submitted again while it is in progress.
      </p>
    </section>
  );
}
