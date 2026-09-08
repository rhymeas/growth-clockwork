import type { ProjectDesk, ReviewArtifact, ReviewStatus } from "./types";

export type ContextualWorld = "understand" | "create" | "learn";
export type ContextualStageState = "active" | "done" | "waiting" | "blocked";

export interface ContextualFlow {
  focus: ContextualWorld;
  title: string;
  detail: string;
  actionLabel: string;
  target: "brief" | "research" | "review" | "insights";
  reviewFilter?: ReviewStatus;
  stages: Array<{ id: ContextualWorld; label: string; state: ContextualStageState }>;
}

/** A navigation read model only. No inferred dispatch, approval, or publication. */
export function getContextualFlow(desk: ProjectDesk, reviews: ReviewArtifact[]): ContextualFlow {
  const scopedReviews = reviews.filter((review) =>
    review.projectId === desk.projectId && review.projectProfileRevision === desk.projectProfileRevision,
  );
  const runtime = desk.automation;
  const pending = scopedReviews.filter((review) => review.status === "pending");
  const rework = scopedReviews.filter((review) => review.status === "rework_requested");
  const approved = scopedReviews.some((review) => review.status === "approved");
  const declined = scopedReviews.some((review) => review.status === "declined");
  const understanding = desk.readiness.steps.filter((step) => step.id !== "baseline");
  const understandingState: ContextualStageState = understanding.some((step) => step.status === "blocked")
    ? "blocked"
    : understanding.length > 0 && understanding.every((step) => step.status === "complete")
      ? "done"
      : "waiting";

  function withStages(
    selection: Omit<ContextualFlow, "stages">,
    createState: ContextualStageState = "waiting",
  ): ContextualFlow {
    return {
      ...selection,
      stages: [
        { id: "understand", label: "Understand", state: understandingState === "waiting" && selection.focus === "understand" ? "active" : understandingState },
        { id: "create", label: "Create", state: createState },
        // A recorded starting baseline is not proof of a measured project outcome.
        { id: "learn", label: "Learn", state: desk.baseline.status === "measured" && selection.focus === "learn" ? "active" : "waiting" },
      ],
    };
  }

  if (runtime?.action === "brief_required" || desk.phase === "brief_required" || desk.source === "local_setup") {
    return withStages({
      focus: "understand", title: "Start with one useful outcome",
      detail: "Give this project a goal, an audience, and a question to explore.",
      actionLabel: "Set project direction", target: "brief",
    });
  }

  if (pending.length) {
    return withStages({
      focus: "create", title: pending.length === 1 ? "A draft is ready for you" : `${pending.length} drafts are ready for you`,
      detail: pending.length === 1 ? pending[0].title : "Read the finished work and its sources before deciding on an exact revision.",
      actionLabel: "Review the draft", target: "review", reviewFilter: "pending",
    }, "active");
  }

  if (rework.length) {
    return withStages({
      focus: "create", title: "Your notes guide the next revision",
      detail: "The reviewed version stays unchanged. Its recorded notes define the next revision.",
      actionLabel: "View revision notes", target: "review", reviewFilter: "rework_requested",
    }, "active");
  }

  if (runtime?.action === "blocked") {
    return withStages({
      focus: "understand", title: "One thing needs attention",
      detail: runtime.reason || desk.blockers[0]?.detail || "The saved work cannot continue yet. Check the available context and evidence.",
      actionLabel: "Inspect the context", target: "research",
    }, "blocked");
  }

  if (runtime?.action === "dispatch" || runtime?.action === "ready_to_start") {
    return withStages({
      focus: "create", title: "Work ready to continue",
      detail: runtime.active_roles.length
        ? `Next in the saved route: ${runtime.active_roles.map((role) => role.label).join(" · ")}. This is readiness, not a live-worker confirmation.`
        : "The saved brief has a next work step. Opening its context does not start another run.",
      actionLabel: "View work context", target: "research",
    }, "active");
  }

  if (runtime?.action === "awaiting_review" || runtime?.action === "prepare_rework") {
    return withStages({
      focus: "create", title: "Check the saved review state",
      detail: "The route refers to review work, but no matching item is available in this project revision's queue.",
      actionLabel: "Open the review queue", target: "review",
      reviewFilter: runtime.action === "prepare_rework" ? "rework_requested" : "pending",
    }, "waiting");
  }

  if (declined && !approved) {
    return withStages({
      focus: "create", title: "Keep the decision, leave the draft",
      detail: "This revision was declined. The decision remains available; no new work or publication is implied.",
      actionLabel: "Read the decision", target: "review", reviewFilter: "declined",
    });
  }

  if (approved || runtime?.action === "settled") {
    return withStages({
      focus: "learn", title: desk.baseline.status === "not_measured" ? "What changed is still an open question" : "Compare the work with the starting point",
      detail: approved
        ? "An exact revision was approved. Approval is not publication or measured impact. Check the evidence and measurement gaps."
        : "The saved work cycle has ended. Completed steps do not establish publication or measured impact.",
      actionLabel: "View insights", target: "insights",
    }, approved ? "done" : "waiting");
  }

  return withStages({
    focus: "understand", title: "Find the signal in the sources",
    detail: runtime?.action === "unconfigured"
      ? "No production route is configured. You can still inspect this project's brief and saved research."
      : "Explore the saved research and its limits. No background execution is implied by this view.",
    actionLabel: "Explore research", target: "research",
  });
}
