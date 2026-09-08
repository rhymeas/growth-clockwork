import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { getPlatformAnalytics } from "../api/client";
import { PlatformAnalytics } from "./PlatformAnalytics";

vi.mock("../api/client", () => ({ getPlatformAnalytics: vi.fn() }));
afterEach(() => vi.resetAllMocks());

function open(container: HTMLElement) {
  const details = container.querySelector("details")!;
  details.open = true;
  fireEvent(details, new Event("toggle"));
}

it("shows all five disconnected sources without fake zeros", async () => {
  vi.mocked(getPlatformAnalytics).mockResolvedValue({project_id: "alpha", status: "not_connected", source: "native_platform_analytics", report: null});
  const {container} = render(<PlatformAnalytics projectId="alpha" />);
  open(container);
  expect(await screen.findByText(/YouTube, Instagram, TikTok, X and Pinterest reports are not connected/)).toBeVisible();
  expect(screen.queryByText("0")).not.toBeInTheDocument();
});

it("shows platform-specific labels, windows and a verified zero", async () => {
  vi.mocked(getPlatformAnalytics).mockResolvedValue({project_id: "alpha", status: "verified", source: "native_platform_analytics", report: {
    fetched_at: "2026-09-07T10:00:00Z", report_sha256: "b".repeat(64), platforms: [{
      platform: "youtube", window: {start_date: "2026-09-01", end_date: "2026-09-06", timezone: "UTC"},
      metrics: [{metric_id: "views", label: "Views", value: 0, unit: "count"}],
    }],
  }});
  const {container} = render(<PlatformAnalytics projectId="alpha" />);
  open(container);
  const youtube = await screen.findByRole("region", {name: "YouTube analytics"});
  expect(within(youtube).getByText("Views")).toBeVisible();
  expect(within(youtube).getByText("0")).toBeVisible();
  expect(within(youtube).getByText("2026-09-01–2026-09-06")).toBeVisible();
  expect(screen.getByText(/not compared across platforms/)).toBeVisible();
});
