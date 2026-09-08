import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getStudio, saveStudio, uploadMaterial, type Studio } from "./studio";

const scope = { project_id: "alpha", project_profile_revision: "alpha-v1" };
const workspace: Studio = {
  ...scope, audiences: [], proposals: [], slots: [], materials: [], suggestions: [],
  capabilities: { platforms: ["website", "medium", "youtube", "instagram", "tiktok", "x", "pinterest"], publishing: false, platform_data: false, max_upload_bytes: 2097152 },
};
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const fetchMock = vi.fn();

describe("studio API boundary", () => {
  beforeEach(() => { fetchMock.mockReset(); vi.stubGlobal("fetch", fetchMock); });
  afterEach(() => vi.unstubAllGlobals());

  it("loads the selected project with cancellation and validates the exact profile", async () => {
    fetchMock.mockResolvedValue(response(workspace));
    const controller = new AbortController();
    await expect(getStudio(scope, controller.signal)).resolves.toEqual(workspace);
    expect(fetchMock).toHaveBeenCalledWith("/api/studio?project_id=alpha", { signal: controller.signal, headers: { Accept: "application/json" } });
  });

  it.each([
    { project_id: "beta" },
    { project_profile_revision: "alpha-v2" },
  ])("rejects mismatched identity before applying a response: %j", async mismatch => {
    fetchMock.mockResolvedValue(response({ studio: { ...workspace, ...mismatch } }));
    await expect(saveStudio(scope, "proposal", { title: "A lesson" }, "request-1")).rejects.toThrow("different project or profile");
  });

  it("sends scope, operation and retry identifier without inventing publishing authority", async () => {
    fetchMock.mockResolvedValue(response({ studio: workspace, automation: { state: "routed", task_id: "task-1", agent_id: "research", inbox_task_id: "inbox-1" } }));
    const payload = { title: "A complete lesson", channel: "pinterest", audience_id: null, material_ids: [] };
    await expect(saveStudio(scope, "proposal", payload, "same-request")).resolves.toEqual({
      studio: workspace, automation: { state: "routed", task_id: "task-1", agent_id: "research", inbox_task_id: "inbox-1" },
    });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/studio");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ ...scope, kind: "proposal", payload, request_id: "same-request" });
  });

  it("sends exact File bytes through the bounded local binary intake", async () => {
    const studio: Studio = { ...workspace, materials: [{ id: "material-1", name: "notes.md", mime_type: "text/markdown", size_bytes: 24, sha256: "a".repeat(64), processing_status: "processed", processing_note: "Text extracted", extracted_text: "# Notes\nTeach one method.", metadata: {}, created_at: "2026-09-06T01:00:00Z" }] };
    fetchMock.mockResolvedValue(response({ studio, material_id: "material-1" }));
    const file = new File(["# Notes\nTeach one method."], "notes.md", { type: "text/markdown" });
    await expect(uploadMaterial(scope, file, "file-request")).resolves.toEqual({ studio, materialId: "material-1" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/material-assets");
    expect(init.body).toBe(file);
    expect(init.headers).toMatchObject({
      "Content-Type": "application/octet-stream",
      "X-Growth-Project-Id": "alpha",
      "X-Growth-Profile-Revision": "alpha-v1",
      "X-Growth-Request-Id": "file-request",
      "X-Growth-Filename": "notes.md",
      "X-Growth-Mime-Type": "text%2Fmarkdown",
    });
  });

  it.each([undefined, "unknown-material"])("rejects an upload receipt with missing or unknown material identity: %s", async materialId => {
    fetchMock.mockResolvedValue(response({ studio: workspace, material_id: materialId }));
    await expect(uploadMaterial(scope, new File(["Text"], "notes.txt"), "file-request")).rejects.toThrow();
  });

  it("resolves content-deduplicated uploads to the original material identity, not the submitted name", async () => {
    const studio: Studio = { ...workspace, materials: [{ id: "original", name: "original.txt", mime_type: "text/plain", size_bytes: 4, sha256: "a".repeat(64), processing_status: "processed", processing_note: "Text extracted", extracted_text: "Text", metadata: {}, created_at: "2026-09-06T01:00:00Z" }] };
    fetchMock.mockResolvedValue(response({ studio, material_id: "original", created: false }));
    await expect(uploadMaterial(scope, new File(["Text"], "renamed.txt", { type: "text/plain" }), "reuse-request")).resolves.toEqual({ studio, materialId: "original" });
  });

  it("rejects oversized files before reading or dispatching", async () => {
    const file = { size: 64 * 1024 * 1024 + 1, name: "large.mp4" } as File;
    await expect(uploadMaterial(scope, file, "large-request")).rejects.toThrow("64 MiB");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("derives a safe MIME type when the browser leaves it empty", async () => {
    fetchMock.mockResolvedValue(response({ studio: workspace, material_id: "missing" }));
    await expect(uploadMaterial(scope, new File(["notes"], "notes.txt"), "request")).rejects.toThrow();
    expect(fetchMock.mock.calls[0][1].headers["X-Growth-Mime-Type"]).toBe("text%2Fplain");
  });

  it("distinguishes unavailable/non-JSON services, API errors and incomplete workspaces", async () => {
    fetchMock.mockResolvedValueOnce(new Response("<html>proxy</html>", { headers: { "content-type": "text/html" } }))
      .mockResolvedValueOnce(response({ error: { message: "Workspace is read-only." } }, 409))
      .mockResolvedValueOnce(response({ ...workspace, materials: null }));
    await expect(getStudio(scope)).rejects.toThrow("did not return JSON");
    await expect(getStudio(scope)).rejects.toThrow("Workspace is read-only.");
    await expect(getStudio(scope)).rejects.toThrow("incomplete workspace");
  });
});
