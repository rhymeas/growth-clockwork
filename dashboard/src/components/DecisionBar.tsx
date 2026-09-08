import { CheckCircle2, LockKeyhole, RotateCcw, XCircle } from "lucide-react";
import type { ReviewStatus } from "../types";

interface DecisionBarProps {
  busy: boolean;
  status: ReviewStatus;
  onApprove: () => void;
  onNote: () => void;
  onDecline: () => void;
}

export function DecisionBar({ busy, status, onApprove, onNote, onDecline }: DecisionBarProps) {
  if (status !== "pending") {
    const statusCopy =
      status === "approved"
        ? "Approved immutable revision."
        : status === "declined"
          ? "Declined immutable revision."
          : "Rework requested for this immutable revision.";
    const statusIcon =
      status === "approved" ? (
        <CheckCircle2 aria-hidden="true" />
      ) : status === "declined" ? (
        <XCircle aria-hidden="true" />
      ) : (
        <RotateCcw aria-hidden="true" />
      );

    return (
      <footer className="action-bar terminal-action-bar" id="review-actions">
        <div className="revision-lock">
          <span><LockKeyhole aria-hidden="true" /></span>
          <div><strong>Decision recorded</strong><p>This version stays as reviewed.</p></div>
        </div>
        <div className={`terminal-status terminal-${status}`}>
          {statusIcon}
          <strong>{statusCopy}</strong>
        </div>
      </footer>
    );
  }

  return (
    <footer className="action-bar" id="review-actions">
      <div className="revision-lock">
        <span><LockKeyhole aria-hidden="true" /></span>
        <div><strong>Final decision</strong><p>Your choice applies to this version only.</p></div>
      </div>
      <div className="review-actions">
        <button className="button button-primary" type="button" disabled={busy} onClick={onApprove}>Approve</button>
        <button className="button button-outline" type="button" disabled={busy} onClick={onNote}>Add note</button>
        <button className="button button-ghost" type="button" disabled={busy} onClick={onDecline}>Decline</button>
      </div>
    </footer>
  );
}
