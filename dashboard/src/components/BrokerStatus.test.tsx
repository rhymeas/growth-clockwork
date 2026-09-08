import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { BrokerStatus } from "./BrokerStatus";
import { getBrokerStatus } from "../api/client";

vi.mock("../api/client", () => ({ getBrokerStatus: vi.fn() }));
afterEach(() => vi.resetAllMocks());

function expand(container: HTMLElement) {
  const details = container.querySelector("details")!;
  details.open = true;
  fireEvent(details, new Event("toggle"));
}

it("loads only when opened and distinguishes unconfigured from zero", async () => {
  vi.mocked(getBrokerStatus).mockResolvedValue({ project_id: "alpha", state: "not_configured", counts: {} });
  const { container } = render(<BrokerStatus projectId="alpha" />);
  expect(getBrokerStatus).not.toHaveBeenCalled();
  expand(container);
  expect(await screen.findByText(/Task broker not connected/)).toBeVisible();
  expect(screen.queryByText("0")).not.toBeInTheDocument();
});

it("shows real counts and supports refresh without calling them growth", async () => {
  vi.mocked(getBrokerStatus).mockResolvedValue({ project_id: "alpha", state: "connected", counts: { running: 2 } });
  const { container } = render(<BrokerStatus projectId="alpha" />);
  expand(container);
  expect(await screen.findByText("2")).toBeVisible();
  expect(screen.getByText(/Not audience growth/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Refresh task status" }));
  await waitFor(() => expect(getBrokerStatus).toHaveBeenCalledTimes(2));
});

it("shows unavailable on failure", async () => {
  vi.mocked(getBrokerStatus).mockRejectedValue(new Error("unavailable"));
  const { container } = render(<BrokerStatus projectId="alpha" />);
  expand(container);
  expect(await screen.findByText(/Task status unavailable/)).toBeVisible();
});

it("names failed publisher work as reconciliation without offering a blind retry", async () => {
  vi.mocked(getBrokerStatus).mockResolvedValue({
    project_id: "alpha", state: "connected", counts: { failed: 1 }, tasks: [{
      task_id: "publisher-1", agent_id: "publisher", status: "failed",
      attempt: 1, max_attempts: 1, not_before: 0, created_at: 10, updated_at: 11,
    }], truncated: false,
  });
  const { container } = render(<BrokerStatus projectId="alpha" />);
  expand(container);
  expect(await screen.findByText(/Reconciliation required/)).toBeVisible();
  expect(screen.getByText(/will not risk a duplicate post/)).toBeVisible();
  expect(screen.queryByRole("button", { name: /retry/i })).not.toBeInTheDocument();
});

it("refreshes an open background view when Studio queues a task", async () => {
  vi.mocked(getBrokerStatus).mockResolvedValue({ project_id: "alpha", state: "connected", counts: { queued: 1 } });
  const { container } = render(<BrokerStatus projectId="alpha" />);
  expand(container);
  await waitFor(() => expect(getBrokerStatus).toHaveBeenCalledTimes(1));
  window.dispatchEvent(new Event("growth-broker-changed"));
  await waitFor(() => expect(getBrokerStatus).toHaveBeenCalledTimes(2));
});

it("does not display an old project response after switching", async () => {
  let finish!: (value: Awaited<ReturnType<typeof getBrokerStatus>>) => void;
  vi.mocked(getBrokerStatus).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }))
    .mockResolvedValue({ project_id: "beta", state: "not_configured", counts: {} });
  const { container, rerender } = render(<BrokerStatus projectId="alpha" />);
  expand(container);
  await waitFor(() => expect(getBrokerStatus).toHaveBeenCalledTimes(1));
  rerender(<BrokerStatus projectId="beta" />);
  finish({ project_id: "alpha", state: "connected", counts: { running: 99 } });
  expect(await screen.findByText(/Task broker not connected/)).toBeVisible();
  expect(screen.queryByText("99")).not.toBeInTheDocument();
});
