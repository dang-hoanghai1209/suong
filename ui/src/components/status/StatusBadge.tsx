import type { ProductionSummaryStatusV1 } from "../../contracts/v1/production";

const labels: Readonly<Record<ProductionSummaryStatusV1, string>> = {
  PLANNING: "Planning",
  PLANNED: "Planned",
  BLOCKED: "Blocked",
  FAILED: "Failed",
  COMPLETED_WITH_WARNINGS: "Completed with warnings",
  COMPLETED: "Completed",
};

const icons: Readonly<Record<ProductionSummaryStatusV1, string>> = {
  PLANNING: "…",
  PLANNED: "✓",
  BLOCKED: "■",
  FAILED: "×",
  COMPLETED_WITH_WARNINGS: "!",
  COMPLETED: "✓",
};

export function StatusBadge({ status }: { readonly status: ProductionSummaryStatusV1 }) {
  return (
    <span className={`status-badge status-badge--${status.toLowerCase()}`}>
      <span aria-hidden="true">{icons[status]}</span>
      {labels[status]}
    </span>
  );
}
