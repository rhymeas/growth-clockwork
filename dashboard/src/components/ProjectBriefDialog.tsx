import { useEffect, useRef, useState, type FormEvent } from "react";
import { Compass, Plus, X } from "lucide-react";
import type { ProjectBriefInput } from "../types";

interface ProjectBriefDialogProps {
  busy: boolean;
  projectName: string;
  onClose: () => void;
  onSubmit: (brief: ProjectBriefInput) => void;
}

interface BriefFormState {
  goalTitle: string;
  goalDetail: string;
  audienceLabel: string;
  audienceDetail: string;
  successLabel: string;
  successDetail: string;
  baselineStatus: "measured" | "not_measured";
  baselineDetail: string;
  horizonLabel: string;
  horizonDetail: string;
  resourcesDetail: string;
  doNothingDetail: string;
  nonGoals: string;
  researchQuestion: string;
}

const emptyBrief: BriefFormState = {
  goalTitle: "",
  goalDetail: "",
  audienceLabel: "",
  audienceDetail: "",
  successLabel: "",
  successDetail: "",
  baselineStatus: "not_measured",
  baselineDetail: "",
  horizonLabel: "",
  horizonDetail: "",
  resourcesDetail: "",
  doNothingDetail: "",
  nonGoals: "",
  researchQuestion: "",
};

export function ProjectBriefDialog({ busy, projectName, onClose, onSubmit }: ProjectBriefDialogProps) {
  const [form, setForm] = useState(emptyBrief);
  const dialogRef = useRef<HTMLDivElement>(null);
  const firstInputRef = useRef<HTMLInputElement>(null);
  const closeRef = useRef(onClose);
  const busyRef = useRef(busy);
  closeRef.current = onClose;
  busyRef.current = busy;

  const nonGoals = form.nonGoals
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);
  const fieldsReady =
    form.goalTitle.trim().length >= 3 &&
    form.goalDetail.trim().length >= 10 &&
    form.audienceLabel.trim().length >= 3 &&
    form.audienceDetail.trim().length >= 10 &&
    form.successLabel.trim().length >= 3 &&
    form.successDetail.trim().length >= 10 &&
    form.baselineDetail.trim().length >= 10 &&
    form.horizonLabel.trim().length >= 3 &&
    form.horizonDetail.trim().length >= 10 &&
    form.resourcesDetail.trim().length >= 10 &&
    form.doNothingDetail.trim().length >= 10 &&
    form.researchQuestion.trim().length >= 10 &&
    nonGoals.length > 0 &&
    nonGoals.every((item) => item.length >= 3) &&
    new Set(nonGoals.map((item) => item.toLocaleLowerCase())).size === nonGoals.length;

  useEffect(() => {
    const previousFocus = document.activeElement as HTMLElement | null;
    firstInputRef.current?.focus();

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busyRef.current) {
        closeRef.current();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = Array.from(
        dialogRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
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

  function setField(field: Exclude<keyof BriefFormState, "baselineStatus">, value: string) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!fieldsReady) return;
    onSubmit({
      goal: { title: form.goalTitle.trim(), detail: form.goalDetail.trim() },
      audience: { label: form.audienceLabel.trim(), detail: form.audienceDetail.trim() },
      successSignal: { label: form.successLabel.trim(), detail: form.successDetail.trim() },
      baseline: { status: form.baselineStatus, detail: form.baselineDetail.trim() },
      horizon: { label: form.horizonLabel.trim(), detail: form.horizonDetail.trim() },
      resources: { detail: form.resourcesDetail.trim() },
      doNothingOption: { detail: form.doNothingDetail.trim() },
      nonGoals,
      researchQuestion: form.researchQuestion.trim(),
    });
  }

  return (
    <div className="dialog-backdrop project-brief-backdrop" role="presentation">
      <div
        ref={dialogRef}
        className="project-brief-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="project-brief-title"
        aria-describedby="project-brief-description"
      >
        <header className="project-brief-heading">
          <div className="project-brief-title-row">
            <span className="project-brief-icon"><Compass aria-hidden="true" /></span>
            <div>
              <p>Project start · {projectName}</p>
              <h2 id="project-brief-title">Set the project brief</h2>
            </div>
          </div>
          <button className="icon-button" type="button" aria-label="Close project brief" onClick={onClose} disabled={busy}>
            <X aria-hidden="true" />
          </button>
        </header>

        <p className="project-brief-description" id="project-brief-description">
          This gives the engine one bounded starting point. It does not start public work or create a final product.
        </p>

        <form className="project-brief-form" onSubmit={submit}>
          <section className="brief-form-section">
            <span className="section-eyebrow">Project goal</span>
            <div className="brief-form-grid">
              <label>
                Goal title
                <input
                  ref={firstInputRef}
                  value={form.goalTitle}
                  onChange={(event) => setField("goalTitle", event.target.value)}
                  placeholder="What decision should this project make?"
                  minLength={3}
                  maxLength={140}
                  required
                  disabled={busy}
                />
              </label>
              <label className="form-field-wide">
                Goal detail
                <textarea
                  value={form.goalDetail}
                  onChange={(event) => setField("goalDetail", event.target.value)}
                  placeholder="Describe the useful outcome and its boundary."
                  minLength={10}
                  maxLength={1200}
                  required
                  disabled={busy}
                />
              </label>
            </div>
          </section>

          <section className="brief-form-section">
            <span className="section-eyebrow">Audience</span>
            <div className="brief-form-grid">
              <label>
                Audience name
                <input
                  value={form.audienceLabel}
                  onChange={(event) => setField("audienceLabel", event.target.value)}
                  placeholder="Who is this for?"
                  minLength={3}
                  maxLength={140}
                  required
                  disabled={busy}
                />
              </label>
              <label className="form-field-wide">
                Audience situation
                <textarea
                  value={form.audienceDetail}
                  onChange={(event) => setField("audienceDetail", event.target.value)}
                  placeholder="What are they trying to do, and where does it break down?"
                  minLength={10}
                  maxLength={1200}
                  required
                  disabled={busy}
                />
              </label>
            </div>
          </section>

          <section className="brief-form-section">
            <span className="section-eyebrow">Success signal</span>
            <div className="brief-form-grid">
              <label>
                Signal name
                <input
                  value={form.successLabel}
                  onChange={(event) => setField("successLabel", event.target.value)}
                  placeholder="What would make this useful?"
                  minLength={3}
                  maxLength={140}
                  required
                  disabled={busy}
                />
              </label>
              <label className="form-field-wide">
                How it will be checked
                <textarea
                  value={form.successDetail}
                  onChange={(event) => setField("successDetail", event.target.value)}
                  placeholder="Name the observable signal and when it will be reviewed."
                  minLength={10}
                  maxLength={1200}
                  required
                  disabled={busy}
                />
              </label>
            </div>
          </section>

          <details className="brief-working-frame" open>
            <summary>
              <span>
                <strong>Working frame</strong>
                <small>Baseline, timing, resources, and the do-nothing option.</small>
              </span>
            </summary>
            <div className="brief-working-frame-body">
              <div className="brief-form-grid brief-baseline-grid">
                <label>
                  Baseline status
                  <select
                    value={form.baselineStatus}
                    onChange={(event) => setForm((current) => ({
                      ...current,
                      baselineStatus: event.target.value as BriefFormState["baselineStatus"],
                    }))}
                    disabled={busy}
                  >
                    <option value="not_measured">Not measured yet</option>
                    <option value="measured">Measured baseline available</option>
                  </select>
                </label>
                <label className="form-field-wide">
                  Current baseline
                  <textarea
                    value={form.baselineDetail}
                    onChange={(event) => setField("baselineDetail", event.target.value)}
                    placeholder="State what is known or not measured. Do not estimate a number."
                    minLength={10}
                    maxLength={1200}
                    required
                    disabled={busy}
                  />
                </label>
              </div>

              <div className="brief-form-grid">
                <label>
                  Decision horizon
                  <input
                    value={form.horizonLabel}
                    onChange={(event) => setField("horizonLabel", event.target.value)}
                    placeholder="For example: Next research cycle"
                    minLength={3}
                    maxLength={140}
                    required
                    disabled={busy}
                  />
                </label>
                <label className="form-field-wide">
                  Horizon detail
                  <textarea
                    value={form.horizonDetail}
                    onChange={(event) => setField("horizonDetail", event.target.value)}
                    placeholder="When should this decision be reviewed?"
                    minLength={10}
                    maxLength={1200}
                    required
                    disabled={busy}
                  />
                </label>
              </div>

              <div className="brief-form-section-last">
                <label>
                  Resources
                  <textarea
                    value={form.resourcesDetail}
                    onChange={(event) => setField("resourcesDetail", event.target.value)}
                    placeholder="People, time, data access, or limits that matter."
                    minLength={10}
                    maxLength={1200}
                    required
                    disabled={busy}
                  />
                </label>
                <label>
                  Do nothing option
                  <textarea
                    value={form.doNothingDetail}
                    onChange={(event) => setField("doNothingDetail", event.target.value)}
                    placeholder="What remains true if no route is chosen now?"
                    minLength={10}
                    maxLength={1200}
                    required
                    disabled={busy}
                  />
                </label>
              </div>
            </div>
          </details>

          <section className="brief-form-section brief-form-section-last">
            <label>
              Research question
              <textarea
                value={form.researchQuestion}
                onChange={(event) => setField("researchQuestion", event.target.value)}
                placeholder="What do we need to learn before choosing a route?"
                minLength={10}
                maxLength={1200}
                required
                disabled={busy}
              />
            </label>
            <label>
              Not in scope
              <textarea
                value={form.nonGoals}
                onChange={(event) => setField("nonGoals", event.target.value)}
                placeholder={"One boundary per line\nFor example: Do not start a public campaign."}
                minLength={3}
                maxLength={1200}
                required
                disabled={busy}
              />
              <span className="field-hint"><Plus aria-hidden="true" /> One boundary per line.</span>
            </label>
          </section>

          <footer className="project-brief-actions">
            <button className="button button-secondary" type="button" onClick={onClose} disabled={busy}>Cancel</button>
            <button className="button button-primary" type="submit" disabled={busy || !fieldsReady}>
              {busy ? "Starting…" : "Start project"}
            </button>
          </footer>
        </form>
      </div>
    </div>
  );
}
