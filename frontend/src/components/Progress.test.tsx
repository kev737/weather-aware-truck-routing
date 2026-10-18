import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Retry } from "../usePlanner";
import { Progress } from "./Progress";

const retry = (over: Partial<Retry> = {}): Retry => ({
  attempt: 1,
  of: 5,
  at: Date.now() + 30_000,
  missing: 120,
  message: "Weather for 120 more points arrives in about 30 s.",
  paced: true,
  ...over,
});

describe("Progress", () => {
  it("while the last route is searched, says the first ones are already on the map", () => {
    render(<Progress phase="routing" routesFound={2} estimatedS={null} startedAt={null} retry={null} onRetryNow={() => {}} />);
    expect(screen.getByText(/2 routes are on the map/)).toBeInTheDocument();
  });

  it("shows an honest time estimate while the weather loads, and that the map is usable", () => {
    render(<Progress phase="weather" estimatedS={20} startedAt={Date.now()} retry={null} onRetryNow={() => {}} />);
    expect(screen.getByText(/about 20 s left/)).toBeInTheDocument();
    expect(screen.getByText(/explore them while you wait/)).toBeInTheDocument();
  });

  it("a paced wait counts down, with no attempt counter and no Retry now", () => {
    render(<Progress phase="done" estimatedS={null} startedAt={null} retry={retry()} onRetryNow={() => {}} />);
    expect(screen.getByText(/Fetching weather for 120 more points in \d+ s/)).toBeInTheDocument();
    expect(screen.queryByText(/attempt/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry now" })).not.toBeInTheDocument();
  });

  it('at 0 s it says it is fetching "now" while the request runs', () => {
    render(<Progress phase="done" estimatedS={null} startedAt={null} retry={retry({ at: Date.now() - 1 })} onRetryNow={() => {}} />);
    expect(screen.getByText(/Fetching weather for 120 more points now…/)).toBeInTheDocument();
  });

  it("a real failure offers Retry now, and shows the attempt once it's stuck", async () => {
    const onRetryNow = vi.fn();
    render(<Progress phase="done" estimatedS={null} startedAt={null} retry={retry({ paced: false, attempt: 3 })} onRetryNow={onRetryNow} />);
    expect(screen.getByText(/attempt 3 of 5/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry now" }));
    expect(onRetryNow).toHaveBeenCalled();
  });
});
