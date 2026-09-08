import { CheckCircle2, ChevronDown, Eye, FileText, TriangleAlert, XCircle } from "lucide-react";
import type { EvidenceReference, QualityCheck, ReviewArtifact } from "../types";

function QualityIcon({ status }: { status: QualityCheck["status"] }) {
  if (status === "pass") return <CheckCircle2 aria-hidden="true" />;
  if (status === "fail") return <XCircle aria-hidden="true" />;
  return <TriangleAlert aria-hidden="true" />;
}

export function ReviewInspector({ review, onEvidence }: { review: ReviewArtifact; onEvidence: (evidence: EvidenceReference) => void }) {
  return (
    <aside className="details-panel" aria-label="Review support">
      <div className="details-heading">Review support</div>

      <details className="detail-group" open>
        <summary>Sources <ChevronDown aria-hidden="true" /></summary>
        <div className="detail-body evidence-list">
          {review.evidence.map((evidence) => (
            <button key={evidence.id} type="button" className="evidence-row" onClick={() => onEvidence(evidence)}>
              <FileText aria-hidden="true" /><span>{evidence.label}</span><Eye aria-hidden="true" />
            </button>
          ))}
        </div>
      </details>

      <details className="detail-group" open>
        <summary>Review checks <ChevronDown aria-hidden="true" /></summary>
        <div className="detail-body check-list">
          {review.qualityChecks.map((check) => (
            <div className={`check-row check-${check.status}`} key={check.id}>
              <QualityIcon status={check.status} />
              <span>{check.label}</span>
            </div>
          ))}
        </div>
      </details>
    </aside>
  );
}
