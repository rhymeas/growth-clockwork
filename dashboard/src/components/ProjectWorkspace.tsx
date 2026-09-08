import {
  AlertTriangle,
  ArrowRight,
  Check,
  ChevronDown,
  Circle,
  ClipboardCheck,
  Compass,
  Radar,
} from "lucide-react";
import { getContextualFlow } from "../contextualFlow";
import { type GoalLoop, type ProjectDesk, type ReadinessStatus, type ReviewArtifact, type ReviewStatus } from "../types";
import { ResearchFeeds } from "./ResearchFeeds";
import { ProjectFoundation } from "./ProjectFoundation";
import { StudioWorkspace } from "./StudioWorkspace";

function statusLabel(status: ReadinessStatus) {
  if (status === "complete") return "Ready";
  if (status === "active") return "In progress";
  if (status === "blocked") return "Needs attention";
  return "Waiting";
}

function StepIcon({ status }: { status: ReadinessStatus }) {
  if (status === "complete") return <Check aria-hidden="true" />;
  if (status === "blocked") return <AlertTriangle aria-hidden="true" />;
  if (status === "active") return <Radar aria-hidden="true" />;
  return <Circle aria-hidden="true" />;
}

function readinessPercent(desk: ProjectDesk) {
  if (!desk.readiness.total) return 0;
  return Math.min(100, Math.max(0, Math.round((desk.readiness.completed / desk.readiness.total) * 100)));
}

function AutomationProgress({ desk }: { desk: ProjectDesk }) {
  const runtime = desk.automation;
  if (!runtime || runtime.action === "unconfigured" || runtime.action === "brief_required") return null;
  const ready = runtime.action === "awaiting_review";
  const label = ready ? "Ready for your review" : runtime.action === "settled" ? "Saved cycle complete"
    : runtime.action === "blocked" ? "Production needs attention" : runtime.action === "prepare_rework" ? "Revision queued"
    : runtime.action === "ready_to_start" ? "Brief queued for Codex" : "Work ready to continue";
  return <section className="automation-progress" aria-label="Marketing production" aria-live="polite">
    <span className="automation-mark">{ready ? <ClipboardCheck aria-hidden="true" /> : <Radar aria-hidden="true" />}</span>
    <div><span className="section-eyebrow">Codex workhorses</span><h2>{label}</h2>
      <p>{ready ? runtime.final_title : runtime.active_roles.length ? `Next: ${runtime.active_roles.map(role => role.label).join(" · ")}`
        : runtime.reason ?? "Research, writing and review follow the saved project brief."}</p>
      <small>{ready ? "Approve, decline, or leave notes. Nothing is published from this desk."
        : "Saved route state, not a live-worker confirmation. Completed steps do not prove publication."}</small>
    </div>
    {runtime.progress ? <span className="automation-count">{runtime.progress.completed}/{runtime.progress.total}<small>steps</small></span> : null}
  </section>;
}

function SetupNotice({ desk }: { desk: ProjectDesk }) {
  if (desk.source !== "local_setup") return null;
  return (
    <div className="setup-notice" role="status">
      <Compass aria-hidden="true" />
      <span>This project needs a brief before research starts. Your inputs stay local and create no public work.</span>
    </div>
  );
}

function ReadinessList({ desk, compact = false }: { desk: ProjectDesk; compact?: boolean }) {
  return (
    <ol className={compact ? "readiness-list readiness-list-compact" : "readiness-list"}>
      {desk.readiness.steps.map((step) => (
        <li className={`readiness-step readiness-${step.status}`} key={step.id}>
          <span className="readiness-icon"><StepIcon status={step.status} /></span>
          <div className="readiness-copy">
            <div>
              <strong>{step.label}</strong>
              <span className="readiness-state">{statusLabel(step.status)}</span>
            </div>
            <p>{step.detail}</p>
            {step.ownerRole && <small>{step.ownerRole}</small>}
          </div>
        </li>
      ))}
    </ol>
  );
}

function Blockers({ desk }: { desk: ProjectDesk }) {
  const hasBaselineBlocker = desk.blockers.some((blocker) => /baseline|measur|analytics/i.test(`${blocker.title} ${blocker.ownerRole}`));
  const blockers =
    desk.baseline.status === "not_measured" && !hasBaselineBlocker
      ? [
          {
            title: "Baseline not measured",
            detail: desk.baseline.detail,
            ownerRole: "Analytics",
          },
          ...desk.blockers,
        ]
      : desk.blockers;

  return (
    <section className="project-section project-blockers" aria-labelledby="blockers-title">
      <div className="project-section-heading">
        <div>
          <span className="section-eyebrow">Needs attention</span>
          <h2 id="blockers-title">Blockers</h2>
        </div>
        <span className={blockers.length ? "count-pill count-pill-warning" : "count-pill"}>{blockers.length}</span>
      </div>
      {blockers.length ? (
        <div className="blocker-list">
          {blockers.map((blocker) => (
            <article className="blocker-row" key={`${blocker.title}-${blocker.ownerRole}`}>
              <AlertTriangle aria-hidden="true" />
              <div>
                <h3>{blocker.title}</h3>
                <p>{blocker.detail}</p>
                <span>{blocker.ownerRole}</span>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <div className="empty-quiet-state">
          <Check aria-hidden="true" />
          <span>No active blockers are recorded.</span>
        </div>
      )}
    </section>
  );
}

function WorkingFrame({ desk }: { desk: ProjectDesk }) {
  const baselineMeasured = desk.baseline.status === "measured";
  return (
    <details className="project-working-frame">
      <summary>
        <span>
          <strong>Working frame</strong>
          <small>Baseline, timing, resources, and the do-nothing option.</small>
        </span>
        <ChevronDown aria-hidden="true" />
      </summary>
      <dl className="working-frame-list">
        <div>
          <dt>Baseline</dt>
          <dd>
            <span className={baselineMeasured ? "frame-status frame-status-ready" : "frame-status frame-status-blocked"}>
              {baselineMeasured ? "Measured" : "Not measured"}
            </span>
            <p>{desk.baseline.detail}</p>
          </dd>
        </div>
        <div>
          <dt>Decision horizon</dt>
          <dd><strong>{desk.horizon.label}</strong><p>{desk.horizon.detail}</p></dd>
        </div>
        <div>
          <dt>Resources</dt>
          <dd><p>{desk.resources.detail}</p></dd>
        </div>
        <div>
          <dt>Do nothing</dt>
          <dd><p>{desk.doNothingOption.detail}</p></dd>
        </div>
      </dl>
    </details>
  );
}

function NextRoles({ desk }: { desk: ProjectDesk }) {
  return (
    <section className="project-section project-roles" aria-labelledby="roles-title">
      <div className="project-section-heading">
        <div>
          <span className="section-eyebrow">Hand-off</span>
          <h2 id="roles-title">Next responsible roles</h2>
        </div>
      </div>
      <div className="role-list">
        {desk.nextRoles.map((role) => (
          <article className="role-row" key={role.role}>
            <span className={`role-status role-status-${role.status}`} aria-hidden="true" />
            <div>
              <h3>{role.label}</h3>
              <p>{role.focus}</p>
            </div>
            <span className="role-timing">
              {role.status === "active" ? "Now" : role.status === "next" ? "Next" : role.status === "complete" ? "Complete" : "Later"}
            </span>
          </article>
        ))}
      </div>
    </section>
  );
}

function LocalCheck({
  loop,
  busy,
  onRun,
}: {
  loop: GoalLoop | null;
  busy: boolean;
  onRun: () => void;
}) {
  if (!loop) {
    return (
      <section className="local-check-card" aria-labelledby="local-check-title">
        <div className="local-check-heading">
          <div>
            <span className="section-eyebrow">Background work</span>
            <h2 id="local-check-title">Local starting check</h2>
          </div>
          <span className="local-check-state">Unavailable</span>
        </div>
        <p className="local-check-summary">The project workspace loaded, but the local check status is not available. Refresh the desk before starting this step.</p>
      </section>
    );
  }
  const result = loop.status === "completed" ? loop.result : null;
  const complete = result !== null;
  const running = busy || loop.status === "queued";

  return (
    <section className="local-check-card" aria-labelledby="local-check-title" aria-live="polite">
      <div className="local-check-heading">
        <div>
          <span className="section-eyebrow">Background work</span>
          <h2 id="local-check-title">Local starting check</h2>
        </div>
        <span className={`local-check-state local-check-${loop.status}`}>
          {complete ? "Complete" : running ? "Running" : "Ready"}
        </span>
      </div>

      {complete ? (
        <>
          <p className="local-check-summary">{result.summary}</p>
          <dl className="local-check-insights">
            {result.insights.map((insight) => (
              <div key={insight.label}>
                <dt>{insight.label}</dt>
                <dd>
                  <span>{insight.state.replaceAll("_", " ")}</span>
                  <p>{insight.detail}</p>
                </dd>
              </div>
            ))}
          </dl>
          <p className="local-check-next"><strong>Next planning question:</strong> {result.nextStep}</p>
          <details className="local-check-limits">
            <summary>What this did not do</summary>
            <ul>{result.limits.map((limit) => <li key={limit}>{limit}</li>)}</ul>
          </details>
        </>
      ) : (
        <p className="local-check-summary">
          {running
            ? "Checking the saved project brief and pinned local context. This does not call a model, research the web, or take a public action."
            : "Verify the saved project brief and pinned local context before choosing the next research step."
          }
        </p>
      )}

      {!complete ? (
        <button className="button button-secondary" type="button" onClick={onRun} disabled={running}>
          {running ? "Checking context…" : "Run local check"}
        </button>
      ) : null}
    </section>
  );
}

export function ProjectStart({
  desk,
  goalLoop,
  goalLoopBusy,
  reviews,
  queueUnavailable = false,
  onOpenResearch,
  onOpenReview,
  onOpenInsights,
  onRunLocalCheck,
  onStartProject,
}: {
  desk: ProjectDesk;
  goalLoop: GoalLoop | null;
  goalLoopBusy: boolean;
  reviews: ReviewArtifact[];
  queueUnavailable?: boolean;
  onOpenResearch: () => void;
  onOpenReview: (filter?: ReviewStatus) => void;
  onOpenInsights: () => void;
  onRunLocalCheck: () => void;
  onStartProject: () => void;
}) {
  const briefRequired = desk.source === "local_setup" || desk.phase === "brief_required";
  const currentWork = desk.currentWork ?? desk.goal;
  const hasDistinctCurrentWork = currentWork.title !== desk.goal.title || currentWork.detail !== desk.goal.detail;
  const flow = getContextualFlow(desk, reviews);
  const scopedReviews = reviews.filter(review => review.projectId === desk.projectId
    && review.projectProfileRevision === desk.projectProfileRevision);
  const current = scopedReviews.find(review => review.status === flow.reviewFilter)
    ?? scopedReviews.find(review => review.status === "approved") ?? scopedReviews[0];
  const primaryAction = () => {
    if (flow.target === "brief") onStartProject();
    else if (flow.target === "review") onOpenReview(flow.reviewFilter);
    else if (flow.target === "insights") onOpenInsights();
    else onOpenResearch();
  };

  return (
    <main className="world-workspace studio-workspace" aria-labelledby="project-desk-title">
      <SetupNotice desk={desk} />
      <StudioWorkspace key={`${desk.projectId}:${desk.projectProfileRevision}`} desk={desk} current={current} queueUnavailable={queueUnavailable}
        nextTitle={flow.title} nextDetail={flow.detail} nextAction={flow.actionLabel} onNext={primaryAction}
        onResearch={onOpenResearch} onReview={() => onOpenReview(flow.reviewFilter)} onInsights={onOpenInsights} />
      <details className="world-context">
        <summary><span><strong>Project context</strong><small>Goal, facts, boundaries and sources</small></span><ChevronDown aria-hidden="true" /></summary>
        <div className="world-context-grid">
          <section><span className="section-eyebrow">Direction</span><h2>{desk.goal.title}</h2><p>{desk.goal.detail}</p>
            <h3>{desk.audience.label}</h3><p>{desk.audience.detail}</p>
            <h3>{desk.successSignal.label}</h3><p>{desk.successSignal.detail}</p>
            <h3>Not part of this project</h3><ul className="non-goal-list">{desk.nonGoals.map(item => <li key={item}>{item}</li>)}</ul>
            <WorkingFrame desk={desk} />
          </section>
          <section>{hasDistinctCurrentWork && <><span className="section-eyebrow">Current work</span><h2>{currentWork.title}</h2><p>{currentWork.detail}</p></>}
            <span className="section-eyebrow">Saved route</span><h2>Behind the work</h2>
            <AutomationProgress desk={desk} />
            {!briefRequired && <LocalCheck loop={goalLoop} busy={goalLoopBusy} onRun={onRunLocalCheck} />}
            <h3>Research progression</h3><ReadinessList desk={desk} compact />
          </section>
          <section><ProjectFoundation items={desk.foundation ?? []} /><Blockers desk={desk} /><NextRoles desk={desk} /></section>
        </div>
      </details>
    </main>
  );
}

export function ResearchWorkspace({
  desk,
  goalLoop,
  reviews,
  onOpenReview,
}: {
  desk: ProjectDesk;
  goalLoop: GoalLoop | null;
  reviews: ReviewArtifact[];
  onOpenReview: (filter?: ReviewStatus) => void;
}) {
  const completion = readinessPercent(desk);
  const flow = getContextualFlow(desk, reviews);

  return (
    <main className="world-workspace research-world" aria-labelledby="research-title">
      <header className="world-heading"><h1 id="research-title">Research</h1><p>{desk.displayName} · Sources before conclusions</p></header>
      <SetupNotice desk={desk} />
      <div className="world-grid world-grid-open">
        <section className="world-card">
          <header><h2>The question</h2><span>{desk.activeResearch.stage.replaceAll("_", " ")}</span></header>
          <div className="world-body"><h3>{desk.activeResearch.title}</h3><p>{desk.activeResearch.detail}</p>
            <div className="world-note"><span className="section-eyebrow">Next research step</span><p>{desk.activeResearch.nextStep}</p></div>
          </div>
        </section>
        <div className="world-card research-sources"><ResearchFeeds key={desk.projectId} projectId={desk.projectId} /></div>
        <aside className="world-card" aria-label="Research support">
          <header><h2>In context</h2><span>The next useful step</span></header>
          <div className="world-body"><h3>{flow.title}</h3><p>{flow.detail}</p>
            {flow.target === "review" && <button className="button button-primary" type="button" onClick={() => onOpenReview(flow.reviewFilter)}>{flow.actionLabel}<ArrowRight aria-hidden="true" /></button>}
            <details className="context-disclosure"><summary>Research progression</summary>
              <p>{desk.activeResearch.progressLabel}</p>
              <div className="readiness-progress research-progress" aria-label={`${completion}% ready`}><span style={{ width: `${completion}%` }} /></div>
              <ReadinessList desk={desk} compact />
            </details>
            <details className="context-disclosure"><summary>Limits & hand-offs</summary><Blockers desk={desk} /><NextRoles desk={desk} /></details>
            {goalLoop?.status === "completed" && goalLoop.result && <details className="context-disclosure"><summary>Local context check</summary><p>{goalLoop.result.nextStep}</p><p>This was a local check, not web research.</p></details>}
          </div>
        </aside>
      </div>
    </main>
  );
}
