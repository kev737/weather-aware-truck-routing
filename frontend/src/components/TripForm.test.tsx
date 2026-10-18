import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TripForm } from "./TripForm";

describe("TripForm", () => {
  it("shows a problem next to its field instead of submitting, and clears it on edit", async () => {
    const onSubmit = vi.fn();
    render(<TripForm loading={false} onSubmit={onSubmit} />);
    const load = screen.getByLabelText("Load (lb)");
    await userEvent.clear(load);
    await userEvent.type(load, "95000");
    await userEvent.click(screen.getByRole("button", { name: "Plan routes" }));

    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByText("Enter 0 to 80,000 lb.")).toBeInTheDocument();
    expect(load).toHaveAttribute("aria-invalid", "true");
    const described = document.getElementById(load.getAttribute("aria-describedby")!);
    expect(described).toHaveTextContent("Enter 0 to 80,000 lb."); // read out with the field

    await userEvent.clear(load);
    await userEvent.type(load, "40000");
    expect(screen.queryByText(/Enter 0 to 80,000/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Plan routes" }));
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ load_lb: 40000 }));
  });

  it("shows the server's message under the field it belongs to", () => {
    render(<TripForm loading={false} onSubmit={() => {}} serverErrors={{ destination: 'We couldn\'t find "Daytn". Try adding the state, like "Dayton, OH".' }} />);
    const to = screen.getByRole("combobox", { name: /To/ });
    expect(to).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText(/We couldn't find "Daytn"/)).toBeInTheDocument();
  });
});
