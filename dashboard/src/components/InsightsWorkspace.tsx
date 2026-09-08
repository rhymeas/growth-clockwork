import { ArrowRight } from "lucide-react";
import type { ProjectDesk, ReviewArtifact } from "../types";
import { WebsiteAnalytics } from "./WebsiteAnalytics";
import { PlatformAnalytics } from "./PlatformAnalytics";
import { BrokerStatus } from "./BrokerStatus";
import { SystemStatus } from "./SystemStatus";

export function InsightsWorkspace({ desk, reviews, queueUnavailable = false, onOpenResearch, onOpenReview }: {
  desk: ProjectDesk;
  reviews: ReviewArtifact[];
  queueUnavailable?: boolean;
  onOpenResearch: () => void;
  onOpenReview: () => void;
}) {
  const current = reviews.filter(item => item.projectId === desk.projectId && item.projectProfileRevision === desk.projectProfileRevision);
  return <main className="world-workspace insights-world" aria-labelledby="insights-title">
    <header className="world-heading">
      <h1 id="insights-title">What we know so far</h1>
      <p>{desk.displayName} · Evidence, not estimates</p>
    </header>
    {desk.source === "local_setup" && <p className="setup-notice">Local setup outline — not a live project report.</p>}
    <SystemStatus />
    <div className="world-grid world-grid-open">
    <section className="world-card" aria-labelledby="baseline-title">
      <header><h2>Measurement</h2><span>From the saved project brief</span></header>
      <div className="world-body"><h3 id="baseline-title">{desk.baseline.status === "not_measured" ? "No measured baseline yet" : "Baseline recorded in the brief"}</h3>
        <WebsiteAnalytics projectId={desk.projectId} />
        <PlatformAnalytics projectId={desk.projectId} />
        <details className="context-disclosure"><summary>Measurement context</summary>
          <p>{desk.baseline.detail}</p>
          <dl className="working-frame-list">
            <div><dt>Success signal</dt><dd><strong>{desk.successSignal.label}</strong><p>{desk.successSignal.detail}</p></dd></div>
            <div><dt>Decision horizon</dt><dd><strong>{desk.horizon.label}</strong><p>{desk.horizon.detail}</p></dd></div>
          </dl>
        </details>
        <p className="insights-limitation">Missing measurements are unavailable, not 0%. Website results never stand in for native platform results.</p>
      </div>
    </section>
    <section className="world-card" aria-labelledby="production-snapshot-title" tabIndex={0}>
      <header><h2>Production</h2><span>From the shared content queue</span></header>
      <div className="world-body"><h3 id="production-snapshot-title">Production, not performance</h3>
      {queueUnavailable ? <p role="status">Content queue unavailable. Revision counts cannot be shown.</p> : <dl className="insights-counts">
        <div><dt>Awaiting review</dt><dd>{current.filter(item => item.status === "pending").length}</dd></div>
        <div><dt>Approved revisions</dt><dd>{current.filter(item => item.status === "approved").length}</dd></div>
        <div><dt>Revision requested</dt><dd>{current.filter(item => item.status === "rework_requested").length}</dd></div>
      </dl>}
      <p className="insights-limitation">Revision counts from this project only. Approval is not publication, audience reach or proof of demand.</p>
      <BrokerStatus key={desk.projectId} projectId={desk.projectId} /></div>
      <footer className="world-actions"><button className="text-button contextual-action" onClick={() => onOpenReview()}>View content <ArrowRight aria-hidden="true" /></button></footer>
    </section>
    <section className="world-card" tabIndex={0}>
      <header><h2>The next question</h2><span>Audience & research</span></header>
      <div className="world-body"><h3>{desk.audience.label}</h3><p>{desk.activeResearch.nextStep}</p></div>
      <footer className="world-actions"><button className="text-button contextual-action" onClick={onOpenResearch}>Explore the evidence <ArrowRight aria-hidden="true" /></button></footer>
    </section>
    </div>
  </main>;
}
