export const platforms = [
  { id: "website", label: "Website", format: "A complete evergreen article", detail: "Teach the full method with sources, a worked example and clear limits." },
  { id: "medium", label: "Medium", format: "A complete native story", detail: "Write for Medium as its own useful lesson, not as a teaser for another page." },
  { id: "youtube", label: "YouTube", format: "A complete video lesson", detail: "Explain one problem, show a worked example, then recap the method." },
  { id: "instagram", label: "Instagram", format: "A saveable visual lesson", detail: "Teach one method in a carousel or a short, self-contained Reel." },
  { id: "tiktok", label: "TikTok", format: "One clear demonstration", detail: "Show the problem and the useful solution in the video itself." },
  { id: "x", label: "X", format: "A concise explanation", detail: "Write a complete insight or thread. No engagement asks." },
  { id: "pinterest", label: "Pinterest", format: "A useful visual reference", detail: "Make the checklist, diagram or steps useful on the Pin itself." },
] as const;
export type Platform = typeof platforms[number]["id"];
export type Audience = { id: string; label: string; problem: string; status: "hypothesis"; created_at: string };
export type Proposal = { id: string; title: string; channel: Platform; audience_id: string | null; material_ids: string[]; status: "draft"; created_at: string };
export type Slot = { id: string; proposal_id: string; planned_at: string; timezone: string; status: "planned"; created_at: string };
export type Material = { id: string; name: string; mime_type: string; size_bytes: number; sha256: string; processing_status: "processed" | "indexed"; processing_note: string; extracted_text: string; metadata: Record<string, unknown>; created_at: string };
export type Suggestion = { id: string; title: string; channel: Platform; audience_id?: string | null; material_ids: string[]; basis: "format_suggestion" | "weekly_feed_hypothesis"; detail: string; source_urls?: string[] };
export type Studio = {
  project_id: string; project_profile_revision: string;
  audiences: Audience[]; proposals: Proposal[]; slots: Slot[]; materials: Material[]; suggestions: Suggestion[];
  capabilities: { platforms: string[]; publishing: false; platform_data: false; max_upload_bytes: number; max_material_storage_bytes?: number; media_processing?: { local_only: true; config_error: string | null; engines: Record<string, { engine: string; enabled: boolean; available: boolean; model_ready?: boolean }> } };
};
export type StudioScope = { project_id: string; project_profile_revision: string };
export type StudioAutomation = {
  state: "routed" | "processing" | "running" | "awaiting_review" | "failed" | "completed";
  task_id: string; agent_id: "research" | "marketing" | "mavery-qa"; inbox_task_id: string;
} | { state: "not_configured" };

async function read(response: Response, scope: StudioScope): Promise<Studio> {
  if (!(response.headers.get("content-type") ?? "").includes("application/json")) throw new Error("Studio service did not return JSON. Restart the local desk if it was updated.");
  const body = await response.json();
  if (!response.ok) throw new Error(body.error?.message ?? `Studio request failed (${response.status}).`);
  return verifyStudio(body, scope);
}

function verifyStudio(body: { studio?: Studio } & Partial<Studio>, scope: StudioScope): Studio {
  const studio = body.studio ?? body;
  if (studio.project_id !== scope.project_id || studio.project_profile_revision !== scope.project_profile_revision) throw new Error("Studio returned a different project or profile. Nothing was applied to this view.");
  for (const field of ["audiences", "proposals", "slots", "materials", "suggestions"] as const) {
    if (!Array.isArray(studio[field])) throw new Error("Studio returned an incomplete workspace.");
  }
  return studio as Studio;
}

export async function getStudio(scope: StudioScope, signal?: AbortSignal) {
  return read(await fetch(`/api/studio?project_id=${encodeURIComponent(scope.project_id)}`, { signal, headers: { Accept: "application/json" } }), scope);
}

export async function saveStudio(scope: StudioScope, kind: "audience" | "proposal" | "slot" | "cancel_slot", payload: object, requestId: string) {
  const response = await fetch("/api/studio", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...scope, kind, payload, request_id: requestId }) });
  if (!(response.headers.get("content-type") ?? "").includes("application/json")) throw new Error("Studio service did not return JSON. Restart the local desk if it was updated.");
  const body = await response.json();
  if (!response.ok) throw new Error(body.error?.message ?? `Studio request failed (${response.status}).`);
  return { studio: verifyStudio(body, scope), automation: body.automation as StudioAutomation | undefined };
}

export async function uploadMaterial(scope: StudioScope, file: File, requestId: string) {
  if (file.size > 64 * 1024 * 1024) throw new Error(`${file.name}: the local intake limit is 64 MiB.`);
  const extension = file.name.toLowerCase().match(/\.[^.]+$/)?.[0] ?? "";
  const fallbackMime: Record<string, string> = {
    ".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv", ".json": "application/json",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
  };
  const mimeType = file.type || fallbackMime[extension] || "application/octet-stream";
  const encoded = (value: string) => encodeURIComponent(value);
  const response = await fetch("/api/material-assets", { method: "POST", headers: {
    Accept: "application/json", "Content-Type": "application/octet-stream",
    "X-Growth-Project-Id": encoded(scope.project_id),
    "X-Growth-Profile-Revision": encoded(scope.project_profile_revision),
    "X-Growth-Request-Id": encoded(requestId),
    "X-Growth-Filename": encoded(file.name),
    "X-Growth-Mime-Type": encoded(mimeType),
  }, body: file });
  if (!(response.headers.get("content-type") ?? "").includes("application/json")) throw new Error("Material service did not return JSON.");
  const body = await response.json();
  if (!response.ok) throw new Error(body.error?.message ?? `Material request failed (${response.status}).`);
  const studio = verifyStudio(body, scope);
  if (typeof body.material_id !== "string" || !studio.materials.some(item => item.id === body.material_id)) throw new Error("The saved material could not be identified. Retry safely; no file was selected.");
  return { studio, materialId: body.material_id as string };
}
