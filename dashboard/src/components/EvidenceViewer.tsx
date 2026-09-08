import { useEffect, useRef, useState } from "react";
import { FileText, LoaderCircle, ShieldCheck, X, XCircle } from "lucide-react";
import { getEvidence, getReviewMaterial } from "../api/client";
import { customerSections } from "../customerContent";
import type { EvidenceReference, ReviewMaterialDescriptor, VerifiedEvidence, VerifiedReviewMaterial } from "../types";

function materialPacket(content: string): ReviewMaterialDescriptor[] | null {
  try {
    const value = JSON.parse(content) as Record<string, unknown>;
    if (value.material_packet_version !== "1.0" || !Array.isArray(value.materials) || value.materials.length > 10) return null;
    const fields = ["material_id", "record_ref", "record_sha256", "name", "mime_type", "size_bytes", "content_sha256", "processing_status"];
    if (value.materials.some((item) => !item || typeof item !== "object"
      || Object.keys(item).sort().join("\0") !== [...fields].sort().join("\0"))) return null;
    const materials = value.materials as unknown as ReviewMaterialDescriptor[];
    if (materials.some((item) => typeof item.material_id !== "string"
      || typeof item.name !== "string" || typeof item.mime_type !== "string"
      || !Number.isSafeInteger(item.size_bytes) || item.size_bytes < 1
      || !/^[0-9a-f]{64}$/.test(item.content_sha256)
      || !/^[0-9a-f]{64}$/.test(item.record_sha256))) return null;
    return materials;
  } catch {
    return null;
  }
}

function ReviewMaterial({ projectId, packet, material }: {
  projectId: string;
  packet: EvidenceReference;
  material: ReviewMaterialDescriptor;
}) {
  const [loaded, setLoaded] = useState<VerifiedReviewMaterial | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let active = true;
    void getReviewMaterial(projectId, packet, material)
      .then((value) => { if (active) setLoaded(value); })
      .catch(() => { if (active) setFailed(true); });
    return () => { active = false; };
  }, [material, packet, projectId]);
  const source = loaded ? `data:${loaded.mime_type};base64,${loaded.contentBase64}` : "";
  return <article className="review-material-card">
    <div className="review-material-preview">
      {!loaded && !failed && <LoaderCircle className="status-spinner" aria-label="Checking material" />}
      {failed && <span role="alert">Preview unavailable</span>}
      {loaded?.mime_type.startsWith("image/") && <img src={source} alt={`Reviewed material: ${loaded.name}`} />}
      {loaded?.mime_type.startsWith("video/") && <video src={source} controls preload="metadata" aria-label={`Reviewed material: ${loaded.name}`} />}
      {loaded?.mime_type.startsWith("audio/") && <audio src={source} controls aria-label={`Reviewed material: ${loaded.name}`} />}
      {loaded && !/^(image|video|audio)\//.test(loaded.mime_type) && <FileText aria-hidden="true" />}
    </div>
    <div><strong>{material.name}</strong><span>{material.mime_type} · {(material.size_bytes / 1024).toFixed(1)} KiB</span></div>
  </article>;
}

interface EvidenceViewerProps {
  projectId: string;
  evidence: EvidenceReference;
  onClose: () => void;
}

export function EvidenceViewer({ projectId, evidence, onClose }: EvidenceViewerProps) {
  const [result, setResult] = useState<VerifiedEvidence | null>(null);
  const [error, setError] = useState<string | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    let active = true;
    setResult(null);
    setError(null);
    void getEvidence(projectId, evidence)
      .then((loaded) => {
        if (active) setResult(loaded);
      })
      .catch((reason: unknown) => {
        if (!active) return;
        void reason;
        setError("This source could not be shown. Please try again.");
      });
    return () => {
      active = false;
    };
  }, [evidence, projectId]);

  useEffect(() => {
    const previousFocus = document.activeElement as HTMLElement | null;
    closeButtonRef.current?.focus();

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        closeRef.current();
        return;
      }
      if (event.key !== "Tab" || !panelRef.current) return;
      const focusable = Array.from(
        panelRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
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

  const materials = result ? materialPacket(result.content) : null;
  const sourceSections = result && materials === null ? customerSections(result.content) : [];

  return (
    <div
      className="dialog-backdrop evidence-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={panelRef}
        className="evidence-viewer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="evidence-viewer-title"
        aria-describedby="evidence-viewer-description"
      >
        <header className="evidence-viewer-heading">
          <div className="evidence-viewer-title">
            <span className="evidence-viewer-icon"><FileText aria-hidden="true" /></span>
            <div>
              <p>Verified evidence</p>
              <h2 id="evidence-viewer-title">{evidence.label}</h2>
            </div>
          </div>
          <button
            ref={closeButtonRef}
            className="icon-button"
            type="button"
            aria-label="Close evidence viewer"
            onClick={onClose}
          >
            <X aria-hidden="true" />
          </button>
        </header>

        <p className="sr-only" id="evidence-viewer-description">
          This source was checked before it was shown.
        </p>

        <div className="evidence-viewer-body" aria-live="polite">
          {!result && !error && (
            <div className="evidence-viewer-state" role="status">
              <LoaderCircle className="status-spinner" aria-hidden="true" />
              <div><strong>Checking source</strong><span>Preparing a safe source summary…</span></div>
            </div>
          )}

          {error && (
            <div className="evidence-viewer-state evidence-viewer-error" role="alert">
              <XCircle aria-hidden="true" />
              <div><strong>Evidence unavailable</strong><span>{error}</span></div>
            </div>
          )}

          {result && (
            <>
              <div className="evidence-verification">
                <span><ShieldCheck aria-hidden="true" /> Source checked</span>
              </div>
              {materials !== null ? (
                <div className="review-material-grid" aria-label="Exact reviewed material">
                  {materials.map((material) => <ReviewMaterial key={material.material_id} projectId={projectId} packet={evidence} material={material} />)}
                </div>
              ) : <div className="evidence-document" aria-label="Source summary">
                {sourceSections.map((section, index) => (
                  <section className={index === 0 ? "evidence-section first-evidence-section" : "evidence-section"} key={section.id}>
                    <h3>{section.title}</h3>
                    {section.paragraphs.map((paragraph) => <p key={paragraph}>{paragraph}</p>)}
                    {section.bullets.length > 0 && <ul>{section.bullets.map((bullet) => <li key={bullet}>{bullet}</li>)}</ul>}
                  </section>
                ))}
                {sourceSections.length === 0 && (
                  <section className="evidence-section first-evidence-section">
                    <h3>Source ready</h3>
                    <p>The source was checked and is available to support this review.</p>
                  </section>
                )}
              </div>}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
