import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { plan } from "../test/fixtures";
import { RouteComparison } from "./RouteComparison";

const row = (label: string) => screen.getByRole("rowheader", { name: label }).closest("tr")!;

describe("RouteComparison", () => {
  it("names the recommended route and why", () => {
    const p = plan();
    render(<RouteComparison routes={p.routes} plan={p} selectedId="A" onSelect={() => {}} />);
    expect(screen.getByText(/★ Route A/)).toBeInTheDocument();
    expect(screen.getByText("Fastest and clear.")).toBeInTheDocument();
  });

  it("compares the routes on the ranking's criteria, best value in bold", () => {
    const p = plan();
    render(<RouteComparison routes={p.routes} plan={p} selectedId="A" onSelect={() => {}} />);
    const severe = within(row("Severe")).getAllByRole("cell");
    expect(severe.map((c) => c.textContent)).toEqual(["0 mi", "12 mi", "0 mi"]);
    expect(severe[0]).toHaveClass("best");
    expect(severe[1]).not.toHaveClass("best");
    expect(within(row("Score")).getAllByRole("cell")[0]).toHaveClass("best");
  });

  it("clicking a column selects that route", async () => {
    const onSelect = vi.fn();
    const p = plan();
    render(<RouteComparison routes={p.routes} plan={p} selectedId="A" onSelect={onSelect} />);
    await userEvent.click(screen.getByRole("button", { name: /^B/ }));
    expect(onSelect).toHaveBeenCalledWith("B");
  });

  it("marks the recommendation provisional while checkpoints still wait for weather", () => {
    const p = plan({ recommendation: { ...plan().recommendation, provisional: true } });
    render(<RouteComparison routes={p.routes} plan={p} selectedId="A" onSelect={() => {}} />);
    expect(screen.getByText("Provisional")).toBeInTheDocument();
  });

  it("says when nothing is safe, and recommends nothing", () => {
    const p = plan({ recommendation: { route_id: null, reason: "", no_safe_route: true, provisional: false } });
    render(<RouteComparison routes={p.routes} plan={p} selectedId="A" onSelect={() => {}} />);
    expect(screen.getByRole("alert")).toHaveTextContent("No safe route at your departure time.");
    expect(screen.queryByText(/★ Route/)).not.toBeInTheDocument();
  });

  it("asks to recheck when the departure is over 48 hours away", () => {
    const p = plan({ warnings: [{ kind: "far_forecast", message: "Recheck within 48 hours of departure." }] });
    render(<RouteComparison routes={p.routes} plan={p} selectedId="A" onSelect={() => {}} />);
    expect(screen.getByText(/Recheck within 48 hours/)).toBeInTheDocument();
  });

  it("before the weather arrives, shows times and distances and waits on the risk rows", () => {
    const p = plan();
    render(<RouteComparison routes={p.routes} plan={null} selectedId="A" onSelect={() => {}} />);
    expect(within(row("Severe")).getAllByRole("cell").map((c) => c.textContent)).toEqual(["…", "…", "…"]);
    expect(within(row("Travel time")).getAllByRole("cell")[0]).toHaveTextContent("9 h");
  });

  it("warns that wide spacing can miss storms between checkpoints", () => {
    const p = plan({ interval_mi: 50 });
    render(<RouteComparison routes={p.routes} plan={p} selectedId="A" onSelect={() => {}} />);
    expect(screen.getByText(/Checkpoints are 50 mi apart/)).toBeInTheDocument();
  });
});
