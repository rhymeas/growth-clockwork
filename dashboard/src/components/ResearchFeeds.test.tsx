import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { ResearchFeeds } from "./ResearchFeeds";
import { getResearchFeeds } from "../api/client";
vi.mock("../api/client", () => ({ getResearchFeeds: vi.fn() }));

it("shows saved sources as discovery and safely expands feed text", async () => {
  vi.mocked(getResearchFeeds).mockResolvedValue({ project_id: "sample-project", week: "2026-W36", result: {
    fetched_at: "2026-09-06T00:23:43Z", status: "collected", item_count: 1,
    sources: [{ publisher: "Example", feed_url: "https://example.org/feed", status: "ok", items: [
      { title: "<script>not markup</script>", url: "https://example.org/post", published: null, excerpt: "Untrusted text" },
    ] }],
  } });
  render(<ResearchFeeds projectId="sample-project" />);
  await screen.findByText("Example");
  await userEvent.click(screen.getByText("Example"));
  expect(screen.getByText("Untrusted text")).toBeVisible();
  expect(screen.getByRole("link")).toHaveAttribute("href", "https://example.org/post");
  expect(screen.getByText(/not validated evidence/)).toBeVisible();
});

it("does not imply a collection happened when none is saved", async () => {
  vi.mocked(getResearchFeeds).mockResolvedValue({ project_id: "p", week: "2026-W36", result: null });
  render(<ResearchFeeds projectId="p" />);
  expect(await screen.findByText(/No feed collection saved/)).toBeVisible();
});

it("opens a saved analysis without starting another model call", async () => {
  vi.mocked(getResearchFeeds).mockResolvedValue({project_id:"p",week:"2026-W36",
    result:{fetched_at:"2026-09-05T00:00:00Z",status:"collected",item_count:0,sources:[]},
    synthesis:{status:"completed",model:"fixture",analysis:{status:"completed",
      artifact:"## Finding\nA limited sample. [source:S1]",uncertainties:["Demand is unknown"],next_decision:"Find a direct example"},
      sources:[{id:"S1",title:"Evidence",url:"https://example.org/evidence"}]}});
  render(<ResearchFeeds projectId="p" />);
  expect(await screen.findByText("Find a direct example")).toBeVisible();
  expect(screen.getByText("Demand is unknown")).not.toBeVisible();
  await userEvent.click(screen.getByText("Offene Fragen"));
  expect(screen.getByText("Demand is unknown")).toBeVisible();
  await userEvent.click(screen.getByText("Vollständige Auswertung lesen"));
  expect(screen.getByText("Finding")).toBeVisible();
  expect(screen.getByRole("link",{name:"Quelle"})).toHaveAttribute("href","https://example.org/evidence");
});
