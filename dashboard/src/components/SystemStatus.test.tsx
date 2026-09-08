import { render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { getSetupStatus } from "../api/client";
import { SystemStatus } from "./SystemStatus";

vi.mock("../api/client", () => ({ getSetupStatus: vi.fn() }));

it("shows actual local setup without implying remote or automatic publishing", async () => {
  vi.mocked(getSetupStatus).mockResolvedValue({
    desk: "running", background: "active", autostart: { state: "not_installed" },
    research_schedule: { state: "not_installed" },
    remote_access: { state: "local_only", provider: "tailscale-serve" },
    publication: { mode: "review", publisher: "disabled" }, host: "must_be_awake",
  });
  render(<SystemStatus projectId="alpha" />);
  const status = screen.getByRole("region", { name: "System status" });
  expect(await within(status).findByText("This Mac")).toBeVisible();
  expect(within(status).getByText("Active")).toBeVisible();
  expect(within(status).getByText("Review")).toBeVisible();
  expect(within(status).getByText("Manual after Mac restart")).toBeVisible();
  expect(within(status).queryByText("Automatic")).not.toBeInTheDocument();
});
