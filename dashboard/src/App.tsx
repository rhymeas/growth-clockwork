import { useCallback, useEffect, useMemo, useState } from "react";
import { FileCheck2, Radar, XCircle } from "lucide-react";
import {
  getGoalLoop,
  getProjectDesk,
  getProjects,
  getReviews,
  isDemoMode,
  startGoalLoop,
  submitProjectBrief,
  submitReviewAction,
} from "./api/client";
import { ActionDialog } from "./components/ActionDialog";
import { DecisionBar } from "./components/DecisionBar";
import { DocumentPreview } from "./components/DocumentPreview";
import { EvidenceViewer } from "./components/EvidenceViewer";
import { QueueRail, type QueueFilter } from "./components/QueueRail";
import { ReviewInspector } from "./components/ReviewInspector";
import { ProjectStart, ResearchWorkspace } from "./components/ProjectWorkspace";
import { ProjectBriefDialog } from "./components/ProjectBriefDialog";
import { StatusNotice, type FeedbackTone } from "./components/StatusNotice";
import { Topbar, type DeskView } from "./components/Topbar";
import { InsightsWorkspace } from "./components/InsightsWorkspace";
import {
  reviewKey,
  type EvidenceReference,
  type GoalLoop,
  type ProjectDesk,
  type ProjectBriefInput,
  type ProjectSummary,
  type ReviewAction,
  type ReviewActionRequest,
  type ReviewArtifact,
  type ReviewStatus,
} from "./types";

type Theme = "light" | "dark";
type Feedback = { tone: FeedbackTone; message: string };

function wait(milliseconds: number) {
  return new Promise<void>((resolve) => window.setTimeout(resolve, milliseconds));
}

function initialTheme(): Theme {
  const saved = window.localStorage.getItem("review-desk-theme");
  if (saved === "light" || saved === "dark") return saved;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function useAppTheme() {
  const [theme, setTheme] = useState<Theme>(initialTheme);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    window.localStorage.setItem("review-desk-theme", theme);
  }, [theme]);

  return {
    theme,
    toggleTheme: () => setTheme((current) => (current === "light" ? "dark" : "light")),
  };
}

export function App() {
  const { theme, toggleTheme } = useAppTheme();
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [projectId, setProjectId] = useState("");
  const [view, setView] = useState<DeskView>(() => {
    const requested = new URLSearchParams(window.location.search).get("view");
    return requested === "review" || requested === "research" || requested === "insights" ? requested : "project";
  });
  const [projectDesk, setProjectDesk] = useState<ProjectDesk | null>(null);
  const [projectDeskError, setProjectDeskError] = useState<string | null>(null);
  const [goalLoop, setGoalLoop] = useState<GoalLoop | null>(null);
  const [goalLoopBusy, setGoalLoopBusy] = useState(false);
  const [projectBriefOpen, setProjectBriefOpen] = useState(false);
  const [briefBusy, setBriefBusy] = useState(false);
  const [reviews, setReviews] = useState<ReviewArtifact[]>([]);
  const [filter, setFilter] = useState<QueueFilter>("pending");
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [dialog, setDialog] = useState<"note" | "decline" | null>(null);
  const [evidenceViewer, setEvidenceViewer] = useState<EvidenceReference | null>(null);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<Feedback | null>({ tone: "progress", message: "Loading review queue…" });
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void getProjects()
      .then((loaded) => {
        if (!active) return;
        setProjects(loaded);
        const requested = new URLSearchParams(window.location.search).get("project");
        if (requested && !loaded.some(project => project.id === requested)) {
          setLoadError("This project is not available in the local desk.");
          setFeedback({tone: "error", message: "Choose an available project to continue."});
          return;
        }
        setProjectId((current) => current || requested || loaded[0]?.id || "");
        setLoadError(null);
      })
      .catch((error: unknown) => {
        if (!active) return;
        void error;
        const message = "The review service is unavailable. Please try again.";
        setLoadError(message);
        setFeedback({ tone: "error", message });
      });
    return () => {
      active = false;
    };
  }, []);

  const selectedProject = projects.find((project) => project.id === projectId);

  useEffect(() => {
    if (!selectedProject) return;
    const url = new URL(window.location.href);
    url.searchParams.set("project", selectedProject.id);
    url.searchParams.set("view", view);
    window.history.replaceState(null, "", url);
  }, [selectedProject, view]);

  useEffect(() => {
    if (!selectedProject) return;
    let active = true;
    setEvidenceViewer(null);
    setProjectBriefOpen(false);
    setLoadError(null);
    setProjectDeskError(null);
    setProjectDesk(null);
    setGoalLoop(null);
    setReviews([]);
    setGoalLoopBusy(false);
    setFeedback({ tone: "progress", message: "Loading review queue…" });
    void Promise.allSettled([getReviews(selectedProject.id), getProjectDesk(selectedProject), getGoalLoop(selectedProject)])
      .then((results) => {
        if (!active) return;
        const [reviewResult, deskResult, goalLoopResult] = results;

        if (reviewResult.status === "fulfilled") {
          const loaded = reviewResult.value;
          setReviews(loaded);
          setFilter("pending");
          const first = loaded.find((review) => review.status === "pending") ?? loaded[0];
          setSelectedKey(first ? reviewKey(first) : null);
          setLoadError(null);
        } else {
          const detail = "The review service is unavailable. Please try again.";
          setReviews([]);
          setSelectedKey(null);
          setLoadError(detail);
        }

        if (deskResult.status === "fulfilled") {
          setProjectDesk(deskResult.value);
          setProjectDeskError(null);
        } else {
          setProjectDesk(null);
          setProjectDeskError("The project workspace is unavailable. The review queue remains separate.");
        }

        if (goalLoopResult.status === "fulfilled") {
          setGoalLoop(goalLoopResult.value);
        } else {
          setGoalLoop(null);
        }

        if (reviewResult.status === "rejected" && deskResult.status === "rejected") {
          setFeedback({ tone: "error", message: "The local workspace service is unavailable. Please try again." });
        } else if (reviewResult.status === "rejected") {
          setFeedback({ tone: "error", message: "The review queue is unavailable. Project research can still be viewed." });
        } else if (deskResult.status === "rejected") {
          setFeedback({ tone: "error", message: "The project workspace is unavailable. Review remains available." });
        } else if (goalLoopResult.status === "rejected") {
          setFeedback({ tone: "info", message: "Project workspace is ready. The local check status is unavailable right now." });
        } else {
          setFeedback(null);
        }
      });
    return () => {
      active = false;
    };
  }, [selectedProject]);

  const visibleReviews = useMemo(() => reviews.filter((review) => review.status === filter), [filter, reviews]);
  const selectedReview =
    reviews.find(
      (review) => reviewKey(review) === selectedKey && review.status === filter,
    ) ?? visibleReviews[0] ?? null;
  function openContextualReview(preferredFilter?: ReviewStatus) {
    const scoped = reviews.filter(review => review.projectId === projectDesk?.projectId
      && review.projectProfileRevision === projectDesk?.projectProfileRevision);
    const target = scoped.find(review => review.status === (preferredFilter ?? "pending"))
      ?? (!preferredFilter ? scoped.find(review => review.status === "rework_requested") ?? scoped[0] : undefined);
    setFilter(target?.status ?? preferredFilter ?? "pending");
    setSelectedKey(target ? reviewKey(target) : null);
    setView("review");
  }

  useEffect(() => {
    const resolvedKey = selectedReview ? reviewKey(selectedReview) : null;
    if (resolvedKey !== selectedKey) setSelectedKey(resolvedKey);
  }, [selectedKey, selectedReview]);

  const closeDialog = useCallback(() => setDialog(null), []);

  async function performAction(action: ReviewAction, value?: string) {
    if (!selectedReview) return;
    const request: ReviewActionRequest = {
      action,
      project_id: selectedReview.projectId,
      project_profile_revision: selectedReview.projectProfileRevision,
      artifact_id: selectedReview.id,
      artifact_revision: selectedReview.revision,
      artifact_sha256: selectedReview.artifactSha256,
      ...(action === "note" ? { note: value } : {}),
      ...(action === "decline" ? { reason: value } : {}),
    };

    setBusy(true);
    setFeedback({ tone: "progress", message: `Saving ${action} decision…` });
    try {
      const result = await submitReviewAction(request);
      setReviews((current) =>
        current.map((review) =>
          reviewKey(review) === reviewKey(selectedReview)
            ? { ...review, status: result.nextStatus, reviewNote: result.reviewNote, declineReason: result.declineReason }
            : review,
        ),
      );
      setDialog(null);
      setFeedback({
        tone: "success",
        message:
          action === "approve"
            ? result.deliveryStatus === "queued"
              ? `${selectedReview.revisionLabel} approved. Publisher task queued.`
              : result.deliveryStatus === "processing"
                ? `${selectedReview.revisionLabel} approved. Publisher task is processing.`
                : result.deliveryStatus === "completed"
                  ? `${selectedReview.revisionLabel} approved. Publisher delivery completed; live publication still needs platform proof.`
              : result.deliveryStatus === "drafted"
              ? `${selectedReview.revisionLabel} approved. Postiz draft created.`
              : result.deliveryStatus === "scheduled"
                ? `${selectedReview.revisionLabel} approved and scheduled in Postiz.`
                : result.deliveryStatus === "submitted"
                  ? `${selectedReview.revisionLabel} approved and sent to Postiz. Live publication is not verified yet.`
                  : result.automationStatus === "packaged"
              ? `${selectedReview.revisionLabel} approved. Release package ready; nothing was published.`
              : result.automationStatus === "needs_attention"
                ? `${selectedReview.revisionLabel} approved. Release or delivery needs attention; nothing was published.`
                : `${selectedReview.revisionLabel} approved. Nothing was published.`
            : action === "decline"
              ? `${selectedReview.revisionLabel} declined. Nothing was published.`
              : "Revision note saved. A new revision was requested; nothing was published.",
      });
    } catch (error) {
      void error;
      setFeedback({
        tone: "error",
        message: "The decision could not be saved. Please try again.",
      });
    } finally {
      setBusy(false);
    }
  }

  async function startProject(brief: ProjectBriefInput) {
    if (!selectedProject) return;
    setBriefBusy(true);
    setFeedback({ tone: "progress", message: "Saving project brief…" });
    try {
      const savedDesk = await submitProjectBrief(selectedProject, brief);
      setProjectDesk(savedDesk);
      setProjectDeskError(null);
      setProjectBriefOpen(false);
      setGoalLoopBusy(true);
      try {
        const localCheck = await runGoalLoop(selectedProject);
        setGoalLoop(localCheck);
        const refreshed = await getProjectDesk(selectedProject);
        setProjectDesk(refreshed);
        setFeedback({
          tone: localCheck.status === "completed" ? "success" : "progress",
          message:
            localCheck.status === "completed"
              ? "Project brief saved. Local check complete; research still needs real evidence."
              : "Project brief saved. The local check is continuing in the background.",
        });
      } catch (refreshError) {
        void refreshError;
        setFeedback({ tone: "info", message: "Project brief saved. The local check or refreshed project view needs a retry." });
      }
    } catch (error) {
      void error;
      setFeedback({ tone: "error", message: "The project brief could not be saved. Please try again." });
    } finally {
      setGoalLoopBusy(false);
      setBriefBusy(false);
    }
  }

  async function runGoalLoop(project: ProjectSummary): Promise<GoalLoop> {
    let current = await startGoalLoop(project);
    for (let attempt = 0; current.status === "queued" && attempt < 12; attempt += 1) {
      await wait(150);
      current = await getGoalLoop(project);
    }
    return current;
  }

  async function startLocalCheck() {
    if (!selectedProject) return;
    setGoalLoopBusy(true);
    setFeedback({ tone: "progress", message: "Checking saved project context locally…" });
    try {
      const current = await runGoalLoop(selectedProject);
      setGoalLoop(current);
      setFeedback({
        tone: current.status === "completed" ? "success" : "progress",
        message:
          current.status === "completed"
            ? "Local check complete. It did not run research, call a model, or take a public action."
            : "Local check is continuing in the background.",
      });
    } catch (error) {
      void error;
      setFeedback({ tone: "error", message: "The local check could not start. The project brief remains saved." });
    } finally {
      setGoalLoopBusy(false);
    }
  }

  return (
    <div className="app-shell">
      <Topbar
        projects={projects}
        projectId={projectId}
        theme={theme}
        demoMode={isDemoMode}
        view={view}
        onProjectChange={(nextProjectId) => {
          setProjectDesk(null);
          setReviews([]);
          setSelectedKey(null);
          setGoalLoop(null);
          setDialog(null);
          setProjectId(nextProjectId);
          setProjectBriefOpen(false);
        }}
        onThemeToggle={toggleTheme}
        onViewChange={setView}
      />

      {view === "project" && projectDesk ? (
        <ProjectStart
          desk={projectDesk}
          goalLoop={goalLoop}
          goalLoopBusy={goalLoopBusy}
          reviews={reviews}
          queueUnavailable={loadError !== null}
          onOpenResearch={() => setView("research")}
          onOpenReview={openContextualReview}
          onOpenInsights={() => setView("insights")}
          onRunLocalCheck={() => void startLocalCheck()}
          onStartProject={() => setProjectBriefOpen(true)}
        />
      ) : view === "research" && projectDesk ? (
        <ResearchWorkspace desk={projectDesk} goalLoop={goalLoop} reviews={reviews} onOpenReview={openContextualReview} />
      ) : view === "insights" && projectDesk ? (
        <InsightsWorkspace desk={projectDesk} reviews={reviews} queueUnavailable={loadError !== null} onOpenResearch={() => setView("research")} onOpenReview={openContextualReview} />
      ) : view !== "review" ? (
        <main className="project-unavailable-state">
          <Radar aria-hidden="true" />
          <h1>Project workspace unavailable</h1>
          <p>{projectDeskError ?? "Loading the project summary…"}</p>
          {loadError ? <button className="button button-secondary" type="button" onClick={() => setView("review")}>Open review</button> : null}
        </main>
      ) : (
        <div className="workspace">
          <QueueRail
            filter={filter}
            reviews={reviews}
            selectedKey={selectedReview ? reviewKey(selectedReview) : null}
            onFilterChange={setFilter}
            onSelect={setSelectedKey}
          />

          {selectedReview && selectedProject ? (
            <>
              <DocumentPreview review={selectedReview} projectName={selectedProject.name} />
              <ReviewInspector
                review={selectedReview}
                onEvidence={setEvidenceViewer}
              />
              <DecisionBar
                busy={busy}
                status={selectedReview.status}
                onApprove={() => void performAction("approve")}
                onNote={() => setDialog("note")}
                onDecline={() => setDialog("decline")}
              />
            </>
          ) : loadError ? (
            <main className="no-selection unavailable-state">
              <XCircle aria-hidden="true" />
              <h1>Review service unavailable</h1>
              <p>{loadError}</p>
              <p>The current revision has not changed.</p>
            </main>
          ) : (
            <main className="no-selection">
              <FileCheck2 aria-hidden="true" />
              <h1>No {filter} revision selected</h1>
              <p>Choose another queue or project to continue.</p>
            </main>
          )}
        </div>
      )}

      {feedback && (
        <StatusNotice tone={feedback.tone} message={feedback.message} onDismiss={() => setFeedback(null)} />
      )}

      {dialog && selectedReview && (
        <ActionDialog
          mode={dialog}
          revisionLabel={selectedReview.revisionLabel}
          busy={busy}
          onClose={closeDialog}
          onSubmit={(value) => void performAction(dialog, value)}
        />
      )}

      {projectBriefOpen && selectedProject && (
        <ProjectBriefDialog
          busy={briefBusy}
          projectName={selectedProject.name}
          onClose={() => setProjectBriefOpen(false)}
          onSubmit={(brief) => void startProject(brief)}
        />
      )}

      {evidenceViewer && selectedReview && (
        <EvidenceViewer
          projectId={selectedReview.projectId}
          evidence={evidenceViewer}
          onClose={() => setEvidenceViewer(null)}
        />
      )}
    </div>
  );
}
