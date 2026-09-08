import { AlertCircle, CheckCircle2, Info, LoaderCircle, X } from "lucide-react";

export type FeedbackTone = "progress" | "success" | "error" | "info";

interface StatusNoticeProps {
  tone: FeedbackTone;
  message: string;
  onDismiss: () => void;
}

export function StatusNotice({ tone, message, onDismiss }: StatusNoticeProps) {
  const icon =
    tone === "error" ? (
      <AlertCircle aria-hidden="true" />
    ) : tone === "success" ? (
      <CheckCircle2 aria-hidden="true" />
    ) : tone === "progress" ? (
      <LoaderCircle className="status-spinner" aria-hidden="true" />
    ) : (
      <Info aria-hidden="true" />
    );

  return (
    <div className={`status-notice status-${tone}`} role={tone === "error" ? "alert" : "status"} aria-live={tone === "error" ? "assertive" : "polite"}>
      {icon}
      <span>{message}</span>
      <button type="button" aria-label="Dismiss status" onClick={onDismiss}>
        <X aria-hidden="true" />
      </button>
    </div>
  );
}
