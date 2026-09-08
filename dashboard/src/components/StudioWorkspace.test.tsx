import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { StudioWorkspace } from "./StudioWorkspace";
import type { ProjectDesk } from "../types";
import type { Studio } from "../api/studio";

vi.mock("../api/client", () => ({
  getWebsiteAnalytics: vi.fn().mockResolvedValue({project_id: "alpha", status: "not_connected", source: "ga4", report: null}),
}));

const scope = { project_id: "alpha", project_profile_revision: "alpha-v1" };
const emptyStudio = (): Studio => ({
  ...scope, audiences: [], proposals: [], slots: [], materials: [], suggestions: [],
  capabilities: { platforms: ["website", "medium", "youtube", "instagram", "tiktok", "x", "pinterest"], publishing: false, platform_data: false, max_upload_bytes: 2097152 },
});
const desk: ProjectDesk = {
  projectId: "alpha", projectProfileRevision: "alpha-v1", displayName: "Alpha", phase: "Research",
  goal: { title: "Teach one useful method", detail: "Evidence first" }, audience: { label: "Operators", detail: "People learning to evaluate a claim." },
  successSignal: { label: "Useful lesson", detail: "Not measured" }, baseline: { status: "not_measured", detail: "No data" },
  horizon: { label: "Next lesson", detail: "One cycle" }, resources: { detail: "Local" }, doNothingOption: { detail: "Keep researching" },
  nonGoals: [], readiness: { completed: 0, total: 0, steps: [] }, blockers: [], nextRoles: [],
  activeResearch: { title: "How do people verify claims?", stage: "Research", progressLabel: "Open", detail: "Find evidence", nextStep: "Read sources" }, source: "api",
};
const response = (studio: Studio, materialId?: string) => new Response(JSON.stringify({ studio, ...(materialId ? { material_id: materialId } : {}) }), { headers: { "content-type": "application/json" } });
const queuedResponse = (studio: Studio) => new Response(JSON.stringify({ studio, automation: { state: "routed", task_id: "task-1", agent_id: "research", inbox_task_id: "inbox-1" } }), { headers: { "content-type": "application/json" } });
const fetchMock = vi.fn();
const props = () => ({ desk, queueUnavailable: false, nextTitle: "Keep researching", nextDetail: "No publication is running.", nextAction: "Continue research", onNext: vi.fn(), onResearch: vi.fn(), onReview: vi.fn(), onInsights: vi.fn() });
const writes = () => fetchMock.mock.calls.filter(([url, init]) => url === "/api/studio" && init?.method === "POST").map(([url, init]) => ({ url, body: JSON.parse(init.body) }));
const uploads = () => fetchMock.mock.calls.filter(([url, init]) => url === "/api/material-assets" && init?.method === "POST").map(([url, init]) => ({ url, body: init.body, headers: init.headers }));
async function ready() { await waitFor(() => expect(screen.queryByText("Loading saved planning & material…")).not.toBeInTheDocument()); }

describe("contextual studio interactions with the real API client", () => {
  beforeEach(() => { fetchMock.mockReset(); fetchMock.mockResolvedValue(response(emptyStudio())); vi.stubGlobal("fetch", fetchMock); });
  afterEach(() => vi.unstubAllGlobals());

  it("saves an audience hypothesis without treating it as validated research", async () => {
    const user = userEvent.setup();
    render(<StudioWorkspace {...props()} />); await ready();
    await user.click(screen.getByRole("button", { name: /Audience Questions before assumptions/ }));
    await user.type(screen.getByLabelText("Audience name"), "New editors");
    await user.type(screen.getByLabelText("Problem to investigate"), "How to check a summary");
    fetchMock.mockResolvedValueOnce(response({ ...emptyStudio(), audiences: [{ id: "a1", label: "New editors", problem: "How to check a summary", status: "hypothesis", created_at: "2026-09-06T01:00:00Z" }] }));
    await user.click(screen.getByRole("button", { name: "Save hypothesis" }));
    expect(await screen.findByText("Hypothesis saved. Evidence is still needed.")).toBeVisible();
    expect(screen.getByText("Hypothesis · not validated")).toBeVisible();
    expect(writes()[0].body).toMatchObject({ ...scope, kind: "audience", payload: { label: "New editors", problem: "How to check a summary" } });
    expect(writes()[0].body.request_id).toEqual(expect.any(String));
  });

  it.each(["Website", "Medium", "YouTube", "Instagram", "TikTok", "X", "Pinterest"])("saves %s ideas with the chosen channel, audience and material", async label => {
    const user = userEvent.setup();
    const studio = emptyStudio();
    studio.audiences.push({ id: "a1", label: "Editors", problem: "Verify claims", status: "hypothesis", created_at: "2026-09-06T01:00:00Z" });
    studio.materials.push({ id: "m1", name: "notes.md", mime_type: "text/markdown", size_bytes: 10, sha256: "a".repeat(64), processing_status: "processed", processing_note: "Text extracted", extracted_text: "Notes", metadata: {}, created_at: "2026-09-06T01:00:00Z" });
    fetchMock.mockResolvedValue(response(studio));
    render(<StudioWorkspace {...props()} />); await ready();
    await user.click(screen.getByRole("button", { name: /Planning A place for the next idea/ }));
    await user.click(within(screen.getByRole("group", { name: "Platform" })).getByRole("button", { name: label }));
    await user.type(screen.getByLabelText("Idea title"), "Check a summary");
    await user.selectOptions(screen.getByLabelText("Audience"), "a1");
    await user.click(screen.getByLabelText("notes.md"));
    await user.click(within(screen.getByRole("region", { name: "Planning" })).getByRole("button", { name: "Save idea" }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0].body).toMatchObject({ ...scope, kind: "proposal", payload: { title: "Check a summary", channel: label.toLowerCase(), audience_id: "a1", material_ids: ["m1"] } });
  });

  it("saves a device-local planning date as UTC, then removes only the saved slot", async () => {
    const user = userEvent.setup();
    const studio = emptyStudio();
    studio.proposals.push({ id: "p1", title: "Check a summary", channel: "website", audience_id: null, material_ids: [], status: "draft", created_at: "2026-09-06T01:00:00Z" });
    fetchMock.mockResolvedValue(response(studio));
    render(<StudioWorkspace {...props()} />); await ready();
    await user.click(screen.getByRole("button", { name: /Planning A place for the next idea/ }));
    await user.click(screen.getByRole("button", { name: /Check a summary Draft idea/ }));
    const localTime = "2026-09-12T14:30";
    fireEvent.change(screen.getByLabelText("Planning date"), { target: { value: localTime } });
    const planned = { ...studio, slots: [{ id: "s1", proposal_id: "p1", planned_at: new Date(localTime).toISOString(), timezone: Intl.DateTimeFormat().resolvedOptions().timeZone, status: "planned" as const, created_at: "2026-09-06T01:00:00Z" }] };
    fetchMock.mockResolvedValueOnce(response(planned));
    await user.click(screen.getByRole("button", { name: "Save date" }));
    expect(await screen.findByText("Planning date saved. No publication job was created.")).toBeVisible();
    expect(writes()[0].body).toMatchObject({ ...scope, kind: "slot", payload: { proposal_id: "p1", planned_at: new Date(localTime).toISOString(), timezone: Intl.DateTimeFormat().resolvedOptions().timeZone } });
    fetchMock.mockResolvedValueOnce(response(studio));
    await user.click(screen.getByRole("button", { name: "Remove date" }));
    expect(await screen.findByText("Planning date removed. The idea is still saved.")).toBeVisible();
    expect(writes()[1].body).toMatchObject({ kind: "cancel_slot", payload: { slot_id: "s1" } });
    expect(screen.getByRole("button", { name: /Check a summary Draft idea/ })).toBeVisible();
  });

  it("reuses the request identity after an ambiguous save failure", async () => {
    const user = userEvent.setup();
    render(<StudioWorkspace {...props()} />); await ready();
    await user.type(screen.getByLabelText("New topic or suggestion"), "Teach a method");
    fetchMock.mockRejectedValueOnce(new Error("Connection interrupted"));
    await user.click(screen.getByRole("button", { name: "Save idea" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Connection interrupted");
    fetchMock.mockResolvedValueOnce(response(emptyStudio()));
    await user.click(screen.getByRole("button", { name: "Save idea" }));
    await waitFor(() => expect(writes()).toHaveLength(2));
    expect(writes()[1].body).toEqual(writes()[0].body);
  });

  it("shows that a broker-connected idea is queued and emits a status refresh", async () => {
    const user = userEvent.setup();
    const refresh = vi.fn();
    window.addEventListener("growth-broker-changed", refresh);
    render(<StudioWorkspace {...props()} />); await ready();
    await user.type(screen.getByLabelText("New topic or suggestion"), "Teach a method");
    fetchMock.mockResolvedValueOnce(queuedResponse(emptyStudio()));
    await user.click(screen.getByRole("button", { name: "Save idea" }));
    expect(await screen.findByText("Idea understood and queued for Research. Nothing has been published.")).toBeVisible();
    expect(refresh).toHaveBeenCalledTimes(1);
    window.removeEventListener("growth-broker-changed", refresh);
  });

  it("shows active automatic drafting without claiming publication", async () => {
    const user = userEvent.setup();
    render(<StudioWorkspace {...props()} />); await ready();
    await user.type(screen.getByLabelText("New topic or suggestion"), "Teach a method");
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({
      studio: emptyStudio(), automation: { state: "processing", task_id: "task-1",
        agent_id: "research", inbox_task_id: "inbox-1" },
    }), { headers: { "content-type": "application/json" } }));
    await user.click(screen.getByRole("button", { name: "Save idea" }));
    expect(await screen.findByText("Research is gathering a source packet in the background. Nothing has been published.")).toBeVisible();
  });

  it("uploads a real text File, shows its processing receipt and links it to an idea", async () => {
    const user = userEvent.setup();
    render(<StudioWorkspace {...props()} />); await ready();
    const studio = emptyStudio();
    studio.materials.push({ id: "m1", name: "notes.md", mime_type: "text/markdown", size_bytes: 12, sha256: "b".repeat(64), processing_status: "processed", processing_note: "UTF-8 text extracted locally.", extracted_text: "Teach a task", metadata: {}, created_at: "2026-09-06T01:00:00Z" });
    fetchMock.mockResolvedValueOnce(response(studio, "m1"));
    await user.upload(screen.getByLabelText("Upload material"), new File(["Teach a task"], "notes.md", { type: "text/markdown" }));
    expect(await screen.findByText("UTF-8 text extracted locally.")).toBeVisible();
    expect(screen.getByText("Teach a task")).toBeVisible();
    expect(uploads()[0]).toMatchObject({ url: "/api/material-assets", body: expect.any(File), headers: {
      "X-Growth-Project-Id": "alpha", "X-Growth-Profile-Revision": "alpha-v1",
      "X-Growth-Filename": "notes.md", "X-Growth-Mime-Type": "text%2Fmarkdown",
    } });
    await user.click(screen.getByRole("button", { name: "Use in an idea" }));
    expect(screen.getByLabelText("Idea title")).toHaveValue("notes");
    expect(screen.getByLabelText("notes.md")).toBeChecked();
  });

  it("rejects a response from a stale project instead of rendering its records", async () => {
    fetchMock.mockResolvedValueOnce(response({ ...emptyStudio(), project_id: "beta", audiences: [{ id: "a2", label: "Private Beta audience", problem: "Other project", status: "hypothesis", created_at: "2026-09-06T01:00:00Z" }] }));
    render(<StudioWorkspace {...props()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("different project or profile");
    expect(screen.queryByText("Private Beta audience")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Upload material")).toBeDisabled();
  });

  it("accepts a dropped File and retains its request identity when upload is retried", async () => {
    render(<StudioWorkspace {...props()} />); await ready();
    const file = new File(["Local note"], "notes.txt", { type: "text/plain", lastModified: 1000 });
    fetchMock.mockRejectedValueOnce(new Error("Upload connection interrupted"));
    fireEvent.drop(screen.getByRole("button", { name: "Drop files here or browse" }).parentElement!, { dataTransfer: { files: [file] } });
    expect(await screen.findByRole("alert")).toHaveTextContent("Upload connection interrupted");
    const uploaded = { ...emptyStudio(), materials: [{ id: "m-retry", name: "notes.txt", mime_type: "text/plain", size_bytes: 10, sha256: "a".repeat(64), processing_status: "processed" as const, processing_note: "Text extracted", extracted_text: "Local note", metadata: {}, created_at: "2026-09-06T01:00:00Z" }] };
    fetchMock.mockResolvedValueOnce(response(uploaded, "m-retry"));
    fireEvent.drop(screen.getByRole("button", { name: "Drop files here or browse" }).parentElement!, { dataTransfer: { files: [file] } });
    expect(await screen.findByText("1 file saved locally. Processing details are attached to each file.")).toBeVisible();
    expect(uploads()).toHaveLength(2);
    expect(uploads()[1].headers["X-Growth-Request-Id"]).toEqual(uploads()[0].headers["X-Growth-Request-Id"]);
    expect(uploads()[1].body).toBe(uploads()[0].body);
  });

  it("selects newly uploaded material even when an older file has the same name", async () => {
    const user = userEvent.setup();
    const old = { id: "old-notes", name: "notes.md", mime_type: "text/markdown", size_bytes: 8, sha256: "a".repeat(64), processing_status: "processed" as const, processing_note: "Old file extracted.", extracted_text: "Old text", metadata: {}, created_at: "2026-09-06T01:00:00Z" };
    fetchMock.mockResolvedValueOnce(response({ ...emptyStudio(), materials: [old] }));
    render(<StudioWorkspace {...props()} />); await ready();
    fetchMock.mockResolvedValueOnce(response({ ...emptyStudio(), materials: [old, { ...old, id: "new-notes", sha256: "b".repeat(64), processing_note: "New file extracted.", extracted_text: "New text", created_at: "2026-09-06T02:00:00Z" }] }, "new-notes"));
    await user.upload(screen.getByLabelText("Upload material"), new File(["New text"], "notes.md", { type: "text/markdown" }));
    expect(await screen.findByText("New file extracted.")).toBeVisible();
    expect(screen.queryByText("Old file extracted.")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Use in an idea" }));
    const checkboxes = screen.getAllByRole("checkbox", { name: "notes.md" });
    expect(checkboxes[0]).not.toBeChecked();
    expect(checkboxes[1]).toBeChecked();
  });

  it("opens the original material after identical bytes are uploaded with a different filename", async () => {
    const user = userEvent.setup();
    const studio: Studio = { ...emptyStudio(), materials: [{ id: "original", name: "original.txt", mime_type: "text/plain", size_bytes: 4, sha256: "a".repeat(64), processing_status: "processed", processing_note: "Original file extracted.", extracted_text: "Text", metadata: {}, created_at: "2026-09-06T01:00:00Z" }] };
    fetchMock.mockResolvedValueOnce(response(studio));
    render(<StudioWorkspace {...props()} />); await ready();
    fetchMock.mockResolvedValueOnce(response(studio, "original"));
    await user.upload(screen.getByLabelText("Upload material"), new File(["Text"], "renamed.txt", { type: "text/plain" }));
    expect(await screen.findByText("Original file extracted.")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Use in an idea" }));
    expect(screen.getByLabelText("original.txt")).toBeChecked();
    expect(screen.getByLabelText("Idea title")).toHaveValue("original");
  });

  it("rejects oversized batches without making any upload requests", async () => {
    render(<StudioWorkspace {...props()} />); await ready();
    const files = Array.from({ length: 7 }, (_, index) => new File(["Note"], `notes-${index}.txt`, { type: "text/plain" }));
    fireEvent.drop(screen.getByRole("button", { name: "Drop files here or browse" }).parentElement!, { dataTransfer: { files } });
    expect(await screen.findByRole("alert")).toHaveTextContent("Add up to six files at a time.");
    expect(uploads()).toHaveLength(0);
  });

  it("does not apply an old in-flight mutation after a keyed project change", async () => {
    const user = userEvent.setup();
    const currentProps = props();
    const view = render(<StudioWorkspace key="alpha-v1" {...currentProps} />); await ready();
    await user.type(screen.getByLabelText("New topic or suggestion"), "Old project idea");
    let finish!: (response: Response) => void;
    fetchMock.mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve; }));
    await user.click(screen.getByRole("button", { name: "Save idea" }));
    fetchMock.mockResolvedValueOnce(response({ ...emptyStudio(), project_id: "beta", project_profile_revision: "beta-v1" }));
    view.rerender(<StudioWorkspace key="beta-v1" {...currentProps} desk={{ ...desk, projectId: "beta", projectProfileRevision: "beta-v1", displayName: "Beta" }} />); await ready();
    await act(async () => { finish(response({ ...emptyStudio(), audiences: [{ id: "old", label: "Old audience", problem: "Old scope", status: "hypothesis", created_at: "2026-09-06T01:00:00Z" }] })); });
    expect(screen.getByText("Beta")).toBeVisible();
    expect(screen.queryByText("Idea saved. It has not started production or publishing.")).not.toBeInTheDocument();
    expect(screen.queryByText("Old audience")).not.toBeInTheDocument();
    expect(screen.getByLabelText("New topic or suggestion")).toHaveValue("");
  });
});
