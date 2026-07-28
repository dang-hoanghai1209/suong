import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { useNavigate } from "react-router-dom";

import {
  ProductionBackendUnavailableError,
  ProductionContractError,
} from "../api/productionClient";
import { useProductionRepository } from "../app/ProductionRepositoryContext";
import {
  CharacterScopeStep,
  ContentStep,
  CreatingStoryPlanStep,
  CreationStepNavigation,
  ProductionProfileStep,
  ReviewStep,
  type DraftErrors,
} from "../components/productionCreation/CreationWorkflowSteps";
import {
  initialProductionCreationDraft,
  isMeaningfulCreationDraft,
  mapDraftToPlanOnlyRequest,
  type CreationStep,
  type DraftField,
  type DraftValidationIssue,
  type ProductionCreationDraft,
  validateCreationDraft,
  validateCreationStep,
} from "../productionCreation/creationDraft";

interface PresentationError {
  readonly title: string;
  readonly detail: string;
}

function issuesByField(issues: readonly DraftValidationIssue[]): DraftErrors {
  return Object.fromEntries(issues.map((issue) => [issue.field, issue.message]));
}

function stepForField(field: DraftField): CreationStep {
  if (field === "inputMode" || field === "sourceContent") {
    return 1;
  }
  if (
    field === "language" ||
    field === "requestedSceneCount"
  ) {
    return 2;
  }
  return 3;
}

function focusIdForField(field: DraftField): string {
  const ids: Record<DraftField, string> = {
    inputMode: "source-content",
    sourceContent: "source-content",
    language: "production-language",
    requestedSceneCount: "scene-count",
    characterScope: "character-scope-recurring-female",
  };
  return ids[field];
}

export function CreateProductionRunPage() {
  const repository = useProductionRepository();
  const navigate = useNavigate();
  const [draft, setDraft] = useState<ProductionCreationDraft>(
    initialProductionCreationDraft,
  );
  const [currentStep, setCurrentStep] = useState<CreationStep>(1);
  const [maxReachableStep, setMaxReachableStep] = useState<CreationStep>(1);
  const [errors, setErrors] = useState<DraftErrors>({});
  const [requestError, setRequestError] = useState<PresentationError | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [pendingFocusId, setPendingFocusId] = useState<string | null>(null);
  const submissionInFlight = useRef(false);
  const submitted = useRef(false);

  useEffect(() => {
    if (pendingFocusId === null) {
      return;
    }
    document.getElementById(pendingFocusId)?.focus();
    setPendingFocusId(null);
  }, [currentStep, errors, pendingFocusId]);

  useEffect(() => {
    function protectUnsavedDraft(event: BeforeUnloadEvent) {
      if (!submitted.current && isMeaningfulCreationDraft(draft)) {
        event.preventDefault();
        event.returnValue = "";
      }
    }
    window.addEventListener("beforeunload", protectUnsavedDraft);
    return () => window.removeEventListener("beforeunload", protectUnsavedDraft);
  }, [draft]);

  function updateDraft(patch: Partial<ProductionCreationDraft>) {
    setDraft((current) => ({ ...current, ...patch }));
    const changedFields = Object.keys(patch) as DraftField[];
    setErrors((current) => {
      const next = { ...current };
      for (const field of changedFields) {
        delete next[field];
      }
      return next;
    });
    setRequestError(null);
    setMaxReachableStep((reachable) =>
      Math.min(reachable, currentStep) as CreationStep,
    );
  }

  function showValidationIssues(issues: readonly DraftValidationIssue[]) {
    setErrors(issuesByField(issues));
    const first = issues[0];
    if (first) {
      setCurrentStep(stepForField(first.field));
      setMaxReachableStep((reachable) =>
        Math.min(reachable, stepForField(first.field)) as CreationStep,
      );
      setPendingFocusId(focusIdForField(first.field));
    }
  }

  function continueFromCurrentStep() {
    const issues = validateCreationStep(draft, currentStep);
    if (issues.length > 0) {
      showValidationIssues(issues);
      return;
    }
    const nextStep = Math.min(currentStep + 1, 4) as CreationStep;
    setErrors({});
    setCurrentStep(nextStep);
    setMaxReachableStep((reachable) =>
      Math.max(reachable, nextStep) as CreationStep,
    );
  }

  function selectStep(step: CreationStep) {
    if (step <= maxReachableStep && step < 5 && !submitting) {
      setCurrentStep(step);
      setRequestError(null);
    }
  }

  async function submitStoryPlan(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submissionInFlight.current) {
      setRequestError({
        title: "StoryPlan creation already in progress",
        detail: "Wait for the current PLAN_ONLY request to finish before trying again.",
      });
      return;
    }
    if (currentStep !== 4) {
      return;
    }
    const issues = validateCreationDraft(draft);
    if (issues.length > 0) {
      showValidationIssues(issues);
      return;
    }

    submissionInFlight.current = true;
    setSubmitting(true);
    setRequestError(null);
    setCurrentStep(5);
    setMaxReachableStep(5);
    try {
      const request = mapDraftToPlanOnlyRequest(draft);
      const result = await repository.createPlanOnlyRun(request);
      if (result.error !== null) {
        setRequestError({
          title:
            result.error.status === "BLOCKED"
              ? "Planning blocked"
              : "Planning failed",
          detail: result.error.message,
        });
        setCurrentStep(4);
        setMaxReachableStep(4);
      } else if (result.run !== null) {
        submitted.current = true;
        navigate(`/production/runs/${encodeURIComponent(result.run.run_id)}`);
      } else {
        setRequestError({
          title: "Invalid backend response",
          detail: "The PLAN_ONLY backend response failed contract validation.",
        });
        setCurrentStep(4);
        setMaxReachableStep(4);
      }
    } catch (caught) {
      setRequestError({
        title:
          caught instanceof ProductionContractError
            ? "Invalid backend response"
            : "Backend unavailable",
        detail:
          caught instanceof ProductionContractError
            ? "The PLAN_ONLY backend response failed contract validation."
            : caught instanceof ProductionBackendUnavailableError
              ? "The PLAN_ONLY planning backend could not be reached safely."
              : "The PLAN_ONLY planning request ended without a safe response.",
      });
      setCurrentStep(4);
      setMaxReachableStep(4);
    } finally {
      submissionInFlight.current = false;
      setSubmitting(false);
    }
  }

  return (
    <div className="page-stack creation-workflow">
      <header className="page-heading">
        <div>
          <p className="eyebrow">Create StoryPlan</p>
          <h1>Start with a clear plan</h1>
          <p>
            Validate the exact PLAN_ONLY request before creating a StoryPlan. No media
            generation is available here.
          </p>
        </div>
      </header>

      <CreationStepNavigation
        currentStep={currentStep}
        maxReachableStep={maxReachableStep}
        onSelect={selectStep}
      />

      <p className="current-step-announcement" role="status" aria-live="polite">
        Step {currentStep} of 5:{" "}
        {currentStep === 1
          ? "Content"
          : currentStep === 2
            ? "Production profile"
            : currentStep === 3
              ? "Character scope"
              : currentStep === 4
                ? "Review"
                : "Create StoryPlan"}
      </p>

      <form className="form-stack" onSubmit={submitStoryPlan} noValidate>
        <div className="creation-workspace">
          <div>
            {currentStep === 1 ? (
              <ContentStep draft={draft} errors={errors} updateDraft={updateDraft} />
            ) : null}
            {currentStep === 2 ? (
              <ProductionProfileStep
                draft={draft}
                errors={errors}
                updateDraft={updateDraft}
              />
            ) : null}
            {currentStep === 3 ? (
              <CharacterScopeStep
                draft={draft}
                errors={errors}
                updateDraft={updateDraft}
              />
            ) : null}
            {currentStep === 4 ? (
              <ReviewStep draft={draft} editStep={selectStep} />
            ) : null}
            {currentStep === 5 ? <CreatingStoryPlanStep /> : null}
          </div>

          <aside className="draft-summary" aria-labelledby="draft-summary-title">
            <p className="eyebrow">Unsaved draft</p>
            <h2 id="draft-summary-title">PLAN_ONLY setup</h2>
            <dl>
              <div>
                <dt>Input</dt>
                <dd>Topic</dd>
              </div>
              <div>
                <dt>Language</dt>
                <dd>{draft.language === "vi" ? "Vietnamese" : "English"}</dd>
              </div>
              <div>
                <dt>Scenes</dt>
                <dd>{draft.requestedSceneCount}</dd>
              </div>
              <div>
                <dt>Mode</dt>
                <dd>PLAN_ONLY</dd>
              </div>
            </dl>
          </aside>
        </div>

        {requestError ? (
          <section
            className="surface status-callout status-callout--blocking"
            role="alert"
            aria-labelledby="creation-error-title"
          >
            <div>
              <h2 id="creation-error-title">{requestError.title}</h2>
              <p>{requestError.detail}</p>
            </div>
          </section>
        ) : null}

        {currentStep < 4 ? (
          <div className="workflow-actions">
            <button
              type="button"
              className="secondary-action"
              disabled={currentStep === 1}
              onClick={() =>
                setCurrentStep((step) => Math.max(1, step - 1) as CreationStep)
              }
            >
              Back
            </button>
            <button type="button" onClick={continueFromCurrentStep}>
              Continue
            </button>
          </div>
        ) : null}

        {currentStep === 4 ? (
          <div className="workflow-actions">
            <button
              type="button"
              className="secondary-action"
              onClick={() => setCurrentStep(3)}
            >
              Back
            </button>
            <button type="submit" disabled={submitting}>
              Create StoryPlan
            </button>
          </div>
        ) : null}
      </form>
    </div>
  );
}
