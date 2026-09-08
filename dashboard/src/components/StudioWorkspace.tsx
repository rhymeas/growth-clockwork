import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { ArrowLeft, ArrowRight, CalendarDays, ChevronRight, FileText, Lightbulb, Paperclip, Plus, Users, X } from "lucide-react";
import { getStudio, platforms, saveStudio, uploadMaterial, type Platform, type Proposal, type Studio, type StudioScope } from "../api/studio";
import { reviewStatusLabels, type ProjectDesk, type ReviewArtifact } from "../types";
import { WebsiteAnalytics } from "./WebsiteAnalytics";

type World = "audience" | "content" | "planning";
type Props = {
  desk: ProjectDesk; current?: ReviewArtifact; queueUnavailable: boolean;
  nextTitle: string; nextDetail: string; nextAction: string; onNext: () => void;
  onResearch: () => void; onReview: () => void; onInsights: () => void;
};

function Window({ title, icon, children, className = "" }: { title: string; icon: ReactNode; children: ReactNode; className?: string }) {
  return <section className={`studio-window ${className}`}><header>{icon}<span>{title}</span></header><div className="studio-window-body">{children}</div></section>;
}

export function StudioWorkspace(props: Props) {
  const { desk, current, queueUnavailable, nextTitle, nextDetail, nextAction, onNext, onResearch, onReview, onInsights } = props;
  const scope: StudioScope = { project_id: desk.projectId, project_profile_revision: desk.projectProfileRevision };
  const [studio, setStudio] = useState<Studio | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [world, setWorld] = useState<World | null>(null);
  const [channel, setChannel] = useState<Platform>("website");
  const [topic, setTopic] = useState("");
  const [audienceId, setAudienceId] = useState("");
  const [materialIds, setMaterialIds] = useState<string[]>([]);
  const [selectedMaterial, setSelectedMaterial] = useState<string | null>(null);
  const [selectedProposal, setSelectedProposal] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [compact, setCompact] = useState(() => window.matchMedia("(max-width: 900px)").matches);
  const uploadInput = useRef<HTMLInputElement>(null);
  const retryIds = useRef(new Map<string, string>());
  const alive = useRef(true);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 900px)");
    const update = () => setCompact(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  useEffect(() => {
    alive.current = true;
    const controller = new AbortController();
    getStudio(scope, controller.signal).then(setStudio).catch(reason => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Studio could not load.");
    });
    return () => { alive.current = false; controller.abort(); };
    // ProjectStart keys this component by exact project/profile. Old mutations cannot update a new project.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function requestId(key: string) {
    const id = retryIds.current.get(key) ?? crypto.randomUUID();
    retryIds.current.set(key, id);
    return id;
  }
  async function save(kind: "audience" | "proposal" | "slot" | "cancel_slot", payload: object, success: string) {
    if (busy || !studio) return false;
    const key = `${kind}:${JSON.stringify(payload)}`;
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await saveStudio(scope, kind, payload, requestId(key));
      retryIds.current.delete(key);
      if (alive.current) {
        setStudio(result.studio);
        setNotice(result.automation?.state === "processing"
          ? result.automation.agent_id === "research"
            ? "Research is gathering a source packet in the background. Nothing has been published."
            : "Marketing is preparing the draft in the background. Nothing has been published."
          : result.automation?.state === "awaiting_review"
            ? "Draft is ready in Content & review. Nothing has been published."
            : result.automation?.state === "failed"
              ? "Drafting needs attention. Open Background tasks for status."
          : result.automation?.state === "routed"
            ? result.automation.agent_id === "research"
              ? "Idea understood and queued for Research. Nothing has been published."
              : "Research finished and the idea is queued for Marketing. Nothing has been published."
          : result.automation?.state === "not_configured"
            ? "Idea saved locally. Background automation is not connected yet."
            : success);
        if (result.automation && result.automation.state !== "not_configured") window.dispatchEvent(new Event("growth-broker-changed"));
      }
      return true;
    } catch (reason) {
      if (alive.current) setError(reason instanceof Error ? reason.message : "Could not save. Retry uses the same request.");
      return false;
    } finally { if (alive.current) setBusy(false); }
  }
  async function upload(files: FileList | File[]) {
    if (busy || !studio) return;
    const selected = Array.from(files);
    if (selected.length > 6) { setError("Add up to six files at a time."); return; }
    setBusy(true); setError(""); setNotice(""); setWorld("content");
    let count = 0;
    const failures: string[] = [];
    for (const file of selected) {
      if (!alive.current) break;
      const key = `file:${file.name}:${file.size}:${file.lastModified}`;
      try {
        const result = await uploadMaterial(scope, file, requestId(key));
        retryIds.current.delete(key);
        if (alive.current) { setStudio(result.studio); setSelectedMaterial(result.materialId); }
        count++;
      } catch (reason) { failures.push(reason instanceof Error ? reason.message : `${file.name}: upload failed.`); }
    }
    if (alive.current) {
      setBusy(false); setError(failures.join(" "));
      if (count) setNotice(`${count} file${count === 1 ? "" : "s"} saved locally. Processing details are attached to each file.`);
    }
    if (uploadInput.current) uploadInput.current.value = "";
  }
  async function addTopic(event: FormEvent) {
    event.preventDefault();
    if (!topic.trim()) return;
    const saved = await save("proposal", { title: topic.trim(), channel, audience_id: audienceId || null, material_ids: materialIds }, "Idea saved. It has not started production or publishing.");
    if (saved && alive.current) { setTopic(""); setMaterialIds([]); setWorld("planning"); }
  }
  const channelInfo = platforms.find(item => item.id === channel)!;
  const proposals = studio?.proposals.filter(item => item.channel === channel) ?? [];
  const material = studio?.materials.find(item => item.id === selectedMaterial);
  const proposal = studio?.proposals.find(item => item.id === selectedProposal);
  const nextSlot = studio?.slots.slice().sort((a, b) => a.planned_at.localeCompare(b.planned_at))[0];
  const nextSlotProposal = studio?.proposals.find(item => item.id === nextSlot?.proposal_id);

  const chooseWorld = (next: World) => { setWorld(next); setNotice(""); };
  const nextStep = <section className="studio-next" aria-label="Next step"><h3>{nextTitle}</h3><p>{nextDetail}</p><button className="studio-pill studio-primary" onClick={onNext}>{nextAction}<ArrowRight aria-hidden="true" /></button></section>;
  const channelPicker = <div className="studio-channels" role="group" aria-label="Platform">
    {platforms.map(item => <button type="button" key={item.id} aria-pressed={channel === item.id} onClick={() => { setChannel(item.id); setSelectedProposal(null); }}>{item.label}</button>)}
  </div>;
  const materialInput = <input className="sr-only" ref={uploadInput} type="file" multiple accept=".txt,.md,.csv,.json,.png,.jpg,.jpeg,.webp,.mp3,.wav,.mp4,.webm,.mov" aria-label="Upload material" disabled={busy || !studio} onChange={event => { if (event.target.files) void upload(event.target.files); }} />;
  const dropZone = <div className={`studio-drop ${dragging ? "dragging" : ""}`} onDragOver={event => { event.preventDefault(); setDragging(true); }} onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false); }} onDrop={event => { event.preventDefault(); setDragging(false); void upload(event.dataTransfer.files); }}>
    <Paperclip aria-hidden="true" /><button type="button" className="studio-link" disabled={busy || !studio} onClick={() => uploadInput.current?.click()}>{busy ? "Saving locally…" : "Drop files here or browse"}</button>
    <small>Text, images & short media · 64 MiB each</small>
  </div>;

  return <>
    <form className="studio-locus" onSubmit={addTopic}>
      <label className="sr-only" htmlFor="studio-topic">New topic or suggestion</label>
      <input id="studio-topic" autoComplete="off" placeholder="What would you like to work on?" maxLength={240} value={topic} onChange={event => setTopic(event.target.value)} />
      <button type="submit" className="studio-pill" disabled={!topic.trim() || busy || !studio} aria-label="Save idea"><ArrowRight aria-hidden="true" /></button>
      {topic && <div className="studio-locus-context"><span>Save as a draft idea for</span><select aria-label="Idea platform" value={channel} onChange={event => setChannel(event.target.value as Platform)}>{platforms.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select><small>No production starts until a route is chosen.</small></div>}
    </form>
    <header className="studio-heading"><div><h1 id="project-desk-title">Your working world</h1><p>{desk.displayName}</p></div>{world && <button className="studio-pill" type="button" onClick={() => setWorld(null)}><ArrowLeft aria-hidden="true" />All worlds</button>}</header>
    {error && <p className="studio-feedback" role="alert">{error}</p>}
    {notice && <p className="studio-feedback" role="status">{notice}</p>}
    {!studio && !error && <p className="studio-feedback" role="status">Loading saved planning & material…</p>}
    {materialInput}
    {compact && !world && nextStep}
    <div className="studio-layout">
      <div className="studio-main">
        {!world ? <div className="studio-worlds" aria-label="Project workflow">
          <article className="studio-world" aria-labelledby="audience-world">
            <button className="studio-world-label" onClick={() => chooseWorld("audience")}><Users aria-hidden="true" /><h2 id="audience-world">Audience</h2><p>Questions before assumptions</p><span className="studio-reveal">Explore<ChevronRight aria-hidden="true" /></span></button>
            <div className="studio-previews">
              <Window title="Working question" icon={<Lightbulb aria-hidden="true" />}><h3>{desk.activeResearch.title}</h3><button className="studio-pill studio-reveal" onClick={onResearch}>Open research</button></Window>
              <Window title="Hypotheses" icon={<Users aria-hidden="true" />}><p>{studio?.audiences[0]?.label ?? desk.audience.label}</p><small>{studio?.audiences.length ? "Working hypothesis · not validated" : "No saved hypotheses yet"}</small><button className="studio-pill studio-reveal" onClick={() => chooseWorld("audience")}><Plus aria-hidden="true" />Add hypothesis</button></Window>
            </div>
          </article>
          <article className="studio-world" aria-labelledby="content-world">
            <button className="studio-world-label" onClick={() => chooseWorld("content")}><FileText aria-hidden="true" /><h2 id="content-world">Content</h2><p>One useful idea at a time</p><span className="studio-reveal">Explore<ChevronRight aria-hidden="true" /></span></button>
            <div className="studio-previews">
              <Window title="Current revision" icon={<FileText aria-hidden="true" />}><h3>{queueUnavailable ? "Content queue unavailable" : current?.title ?? "No revision ready yet"}</h3><small>{current && !queueUnavailable ? reviewStatusLabels[current.status] : nextTitle}</small><button className="studio-pill studio-reveal" onClick={onReview}>Open revision</button></Window>
              <Window title="Material" icon={<Paperclip aria-hidden="true" />}>{dropZone}</Window>
            </div>
          </article>
          <article className="studio-world" aria-labelledby="planning-world">
            <button className="studio-world-label" onClick={() => chooseWorld("planning")}><CalendarDays aria-hidden="true" /><h2 id="planning-world">Planning</h2><p>A place for the next idea</p><span className="studio-reveal">Explore<ChevronRight aria-hidden="true" /></span></button>
            <div className="studio-previews">
              <Window title="Channels" icon={<Lightbulb aria-hidden="true" />}>{channelPicker}<button className="studio-pill studio-reveal" onClick={() => chooseWorld("planning")}>Plan for {channelInfo.label}</button></Window>
              <Window title="Schedule" icon={<CalendarDays aria-hidden="true" />}><h3>{nextSlotProposal?.title ?? "No planned slots yet"}</h3>{nextSlot && <small>{new Date(nextSlot.planned_at).toLocaleString()} · planned only</small>}<button className="studio-pill studio-reveal" onClick={() => { chooseWorld("planning"); if (nextSlotProposal) { setChannel(nextSlotProposal.channel); setSelectedProposal(nextSlotProposal.id); } }}>{nextSlotProposal ? "View planned idea" : "Plan an idea"}</button></Window>
            </div>
          </article>
        </div> : <section className="studio-focus" aria-labelledby="studio-focus-title">
          <header className="studio-focus-heading"><h2 id="studio-focus-title">{world === "audience" ? "Audience" : world === "content" ? "Content & material" : "Planning"}</h2><button className="studio-pill" aria-label="Close world" onClick={() => setWorld(null)}><X aria-hidden="true" /></button></header>
          {world === "audience" ? <div className="studio-detail-grid">
            <section><h3>Who are we helping?</h3><p>{desk.audience.detail}</p>
              <div className="studio-records">{studio?.audiences.map(item => <article key={item.id}><small>Hypothesis · not validated</small><h4>{item.label}</h4><p>{item.problem}</p></article>)}</div>
              <form className="studio-form" onSubmit={async event => { event.preventDefault(); const form = event.currentTarget; const data = new FormData(form); if (await save("audience", {label: data.get("label"), problem: data.get("problem")}, "Hypothesis saved. Evidence is still needed.")) form.reset(); }}>
                <label>Audience name<input required name="label" maxLength={160} placeholder="Who, in what situation?" /></label>
                <label>Problem to investigate<textarea required name="problem" maxLength={2000} rows={3} placeholder="What do they struggle with?" /></label>
                <button className="studio-pill studio-primary" disabled={busy || !studio}>Save hypothesis</button>
              </form>
            </section>
            <aside className="studio-side-note"><h3>Start with evidence</h3><p>{desk.activeResearch.title}</p><p>These are working assumptions, not researched personas. Record the question before treating it as a conclusion.</p><button className="studio-pill" onClick={onResearch}>Open research<ArrowRight aria-hidden="true" /></button></aside>
          </div> : world === "content" ? <>
            {dropZone}<p className="studio-muted">Files stay on this Mac. Text is extracted automatically. Images use local Tesseract OCR when available; audio and video use local FFmpeg + whisper.cpp only when configured. No API or platform receives your material.</p>
            <div className="studio-detail-grid"><section><h3>Material library</h3>{!studio?.materials.length && <p>No material yet. Add notes, source text, images or a short clip.</p>}
              <div className="studio-records">{studio?.materials.map(item => <button key={item.id} className="studio-record-button" aria-pressed={selectedMaterial === item.id} onClick={() => setSelectedMaterial(item.id)}><FileText aria-hidden="true" /><span><strong>{item.name}</strong><small>{(item.size_bytes / 1024).toFixed(1)} KiB · {item.processing_status === "processed" ? "Processed locally" : "Indexed · setup may be needed"}</small></span><ChevronRight aria-hidden="true" /></button>)}</div>
              {material && <article className="studio-material-detail"><h4>{material.name}</h4><p>{material.processing_note}</p>{typeof material.metadata.words === "number" && <p>{material.metadata.words} words · {String(material.metadata.lines)} lines</p>}{material.extracted_text && <pre>{material.extracted_text}</pre>}{material.metadata.preview_truncated === true && <p>Preview limited to 12,000 characters. The original file is preserved locally.</p>}<button className="studio-pill" onClick={() => { setMaterialIds([material.id]); setTopic(material.name.replace(/\.[^.]+$/, "")); setWorld("planning"); }}>Use in an idea<ArrowRight aria-hidden="true" /></button></article>}
            </section><aside className="studio-side-note"><h3>Current revision</h3><p>{queueUnavailable ? "The review queue could not be checked." : current?.title ?? nextTitle}</p><p>{current?.reviewNote ?? nextDetail}</p><button className="studio-pill" onClick={onReview}>Open content & review</button></aside></div>
          </> : <>
            {channelPicker}
            <div className="studio-detail-grid"><section><h3>{channelInfo.format}</h3><p>{channelInfo.detail}</p>
              <form className="studio-form" onSubmit={addTopic}><label>Idea title<input required maxLength={240} value={topic} onChange={event => setTopic(event.target.value)} placeholder="What will someone learn?" /></label>
                <label>Audience<select value={audienceId} onChange={event => setAudienceId(event.target.value)}><option value="">Not assigned yet</option>{studio?.audiences.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
                {!!studio?.materials.length && <fieldset className="studio-material-picker"><legend>Source material</legend>{studio.materials.map(item => <label key={item.id}><input type="checkbox" checked={materialIds.includes(item.id)} onChange={event => setMaterialIds(ids => event.target.checked ? [...ids, item.id] : ids.filter(id => id !== item.id))} />{item.name}</label>)}</fieldset>}
                <button className="studio-pill studio-primary" disabled={busy || !studio}>Save idea</button>
              </form>
              <h3 className="studio-section-heading">Saved ideas</h3>{!proposals.length && <p>No ideas for {channelInfo.label} yet.</p>}
              <div className="studio-records">{proposals.map(item => <button className="studio-record-button" key={item.id} aria-pressed={selectedProposal === item.id} onClick={() => setSelectedProposal(item.id)}><CalendarDays aria-hidden="true" /><span><strong>{item.title}</strong><small>{studio?.slots.some(slot => slot.proposal_id === item.id) ? "Planned · not publishing" : "Draft idea"}</small></span><ChevronRight aria-hidden="true" /></button>)}</div>
              {proposal && <ScheduleForm key={proposal.id} proposal={proposal} studio={studio!} busy={busy} onSave={save} />}
            </section><aside className="studio-side-note"><h3>Suggestions</h3><p>Weekly source hypotheses and local format starters. Neither proves audience demand.</p>
              <div className="studio-records">{studio?.suggestions.filter(item => item.channel === channel).slice(0, 5).map(item => <article key={item.id}><small>{item.basis === "weekly_feed_hypothesis" ? "Weekly research hypothesis" : "Material format starter"}</small><h4>{item.title}</h4><p>{item.detail}</p><button className="studio-pill" disabled={busy} onClick={() => { setTopic(item.title); setAudienceId(item.audience_id ?? ""); setMaterialIds(item.material_ids); }}>Use suggestion</button></article>)}</div>
              {!studio?.suggestions.some(item => item.channel === channel) && <button className="studio-pill" onClick={() => setWorld("content")}>Add source material<Paperclip aria-hidden="true" /></button>}
              <hr /><h3>Planned, not published</h3><p>Dates stay local. After exact approval, an enabled publisher task can schedule them; Background tasks shows its real state.</p>
            </aside></div>
          </>}
        </section>}
      </div>
      <aside className="studio-outcomes" aria-label="Growth and next step">
        <h2>Outcomes</h2><WebsiteAnalytics projectId={desk.projectId} /><button className="studio-pill studio-reveal" onClick={onInsights}>Open insights<ArrowRight aria-hidden="true" /></button>
        {(!compact || world) && nextStep}
        <p className="studio-runtime-note">{desk.authority?.publishConfiguration === "invalid_fail_closed"
          ? "Publishing permission is invalid and safely off."
          : desk.authority?.publishMode === "automatic"
            ? "Automatic publishing is permitted. Connector state appears under Insights."
            : desk.authority?.publishMode === "off"
              ? "Publishing is off."
              : "Publishing requires exact review. Connector state appears under Insights."}</p>
      </aside>
    </div>
  </>;
}

function ScheduleForm({ proposal, studio, busy, onSave }: { proposal: Proposal; studio: Studio; busy: boolean; onSave: (kind: "slot" | "cancel_slot", payload: object, success: string) => Promise<boolean> }) {
  const existing = studio.slots.find(slot => slot.proposal_id === proposal.id);
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const initial = existing ? new Date(new Date(existing.planned_at).getTime() - new Date(existing.planned_at).getTimezoneOffset() * 60000).toISOString().slice(0, 16) : "";
  return <form className="studio-form studio-schedule" onSubmit={event => {
    event.preventDefault(); const date = new Date(String(new FormData(event.currentTarget).get("planned_at"))); if (!Number.isFinite(date.getTime())) return;
    void onSave("slot", { proposal_id: proposal.id, planned_at: date.toISOString(), timezone }, "Planning date saved. No publication job was created.");
  }}><h4>Plan: {proposal.title}</h4><label>Planning date<input key={existing?.id ?? "new"} name="planned_at" type="datetime-local" required defaultValue={initial} /></label><small>{timezone} · your device timezone</small>
    {existing && <p>{new Date(existing.planned_at).getTime() < Date.now() ? "This planned date has passed. Nothing was auto-published." : "A date is already saved. Saving updates the plan."}</p>}
    <div className="studio-form-actions"><button className="studio-pill studio-primary" disabled={busy}>{existing ? "Update date" : "Save date"}</button>{existing && <button type="button" className="studio-pill" disabled={busy} onClick={() => void onSave("cancel_slot", {slot_id: existing.id}, "Planning date removed. The idea is still saved.")}>Remove date</button>}</div>
  </form>;
}
