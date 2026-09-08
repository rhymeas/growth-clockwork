import { useEffect, useRef, useState, type FormEvent } from "react";
import { FileText, X } from "lucide-react";

interface ActionDialogProps {
  mode: "note" | "decline";
  revisionLabel: string;
  busy: boolean;
  onClose: () => void;
  onSubmit: (value: string) => void;
}

export function ActionDialog({ mode, revisionLabel, busy, onClose, onSubmit }: ActionDialogProps) {
  const [value, setValue] = useState("");
  const dialogRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const closeRef = useRef(onClose);
  const busyRef = useRef(busy);
  closeRef.current = onClose;
  busyRef.current = busy;

  const isNote = mode === "note";
  const title = isNote ? "Add a note" : "Decline this version";
  const description = isNote
    ? "Your note requests a fresh version. This version stays exactly as reviewed."
    : "Add a short reason so the next step is clear. This version stays on record.";
  const inputLabel = isNote ? "What should change?" : "Why should this revision be declined?";

  useEffect(() => {
    const previousFocus = document.activeElement as HTMLElement | null;
    inputRef.current?.focus();

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busyRef.current) {
        closeRef.current();
        return;
      }

      if (event.key !== "Tab" || !dialogRef.current) return;

      const focusable = Array.from(
        dialogRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
        ),
      );
      const first = focusable[0];
      const last = focusable.at(-1);

      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      previousFocus?.focus();
    };
  }, []);

  function submit(event: FormEvent) {
    event.preventDefault();
    const trimmed = value.trim();
    if (trimmed) onSubmit(trimmed);
  }

  return (
    <div className="dialog-backdrop" role="presentation">
      <div
        ref={dialogRef}
        className="action-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="action-dialog-title"
        aria-describedby="action-dialog-description"
      >
        <div className="dialog-heading">
          <div>
            <h2 id="action-dialog-title">{title}</h2>
            <p id="action-dialog-description">{description}</p>
          </div>
          <button className="icon-button" type="button" aria-label="Close dialog" onClick={onClose} disabled={busy}>
            <X aria-hidden="true" />
          </button>
        </div>

        <form onSubmit={submit}>
          <label htmlFor="revision-note">{inputLabel}</label>
          <textarea
            ref={inputRef}
            id="revision-note"
            value={value}
            onChange={(event) => setValue(event.target.value)}
            placeholder={isNote ? "Describe the exact change for the next revision." : "Record the decision reason."}
            required
            minLength={3}
            disabled={busy}
          />

          <div className="revision-transition">
            <FileText aria-hidden="true" />
            <span>{isNote ? "Current version → new version" : "Current version stays on record"}</span>
          </div>

          <div className="dialog-actions">
            <button className="button button-secondary" type="button" onClick={onClose} disabled={busy}>
              Cancel
            </button>
            <button className={`button ${isNote ? "button-primary" : "button-danger"}`} type="submit" disabled={busy || value.trim().length < 3}>
              {busy ? "Saving…" : isNote ? "Request new revision" : "Decline revision"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
