import { render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { getWebsiteAnalytics } from "../api/client";
import { WebsiteAnalytics } from "./WebsiteAnalytics";

vi.mock("../api/client", () => ({ getWebsiteAnalytics: vi.fn() }));
afterEach(() => vi.resetAllMocks());

it("keeps missing analytics distinct from zero", async () => {
  vi.mocked(getWebsiteAnalytics).mockResolvedValue({ project_id: "alpha", status: "not_connected", source: "ga4", report: null, setup: { state: "configuration_required" } });
  render(<WebsiteAnalytics projectId="alpha" />);
  const analytics = screen.getByRole("region", {name: "Website analytics"});
  for (const label of ["Active users", "Engaged sessions", "Download clicks"]) expect(within(analytics).getByText(label)).toBeVisible();
  expect(await within(analytics).findByText(/GA4 setup needed/)).toBeVisible();
  expect(within(analytics).getAllByLabelText("Not measured")).toHaveLength(3);
  expect(within(analytics).queryByText("0")).not.toBeInTheDocument();
});

it("shows a verified zero with its exact source window", async () => {
  vi.mocked(getWebsiteAnalytics).mockResolvedValue({ project_id: "alpha", status: "verified", source: "ga4", report: {
    fetched_at: "2026-09-07T10:00:00Z", report_sha256: "a".repeat(64),
    window: { start_date: "2026-08-10", end_date: "2026-09-05", timezone: "America/Vancouver", excluded_latest_days: 2 },
    metrics: { active_users: 12, engaged_sessions: 7, download_clicks: 0 },
  }});
  render(<WebsiteAnalytics projectId="alpha" />);
  const analytics = screen.getByRole("region", {name: "Website analytics"});
  expect(await within(analytics).findByText("12")).toBeVisible();
  expect(within(analytics).getByText("7")).toBeVisible();
  expect(within(analytics).getByText("0")).toBeVisible();
  expect(within(analytics).getByText(/2026-08-10–2026-09-05/)).toBeVisible();
  expect(within(analytics).queryByLabelText("Not measured")).not.toBeInTheDocument();
});

it("fails visibly without retaining an old project's values", async () => {
  let finish!: (value: Awaited<ReturnType<typeof getWebsiteAnalytics>>) => void;
  vi.mocked(getWebsiteAnalytics).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }))
    .mockRejectedValueOnce(new Error("broken"));
  const { rerender } = render(<WebsiteAnalytics projectId="alpha" />);
  rerender(<WebsiteAnalytics projectId="beta" />);
  finish({ project_id: "alpha", status: "verified", source: "ga4", report: {
    fetched_at: "2026-09-07T10:00:00Z", report_sha256: "a".repeat(64),
    window: { start_date: "2026-08-10", end_date: "2026-09-05", timezone: "UTC", excluded_latest_days: 2 },
    metrics: { active_users: 99, engaged_sessions: 2, download_clicks: 1 },
  }});
  expect(await screen.findByText(/GA4 report unavailable/)).toBeVisible();
  expect(screen.queryByText("99")).not.toBeInTheDocument();
});
