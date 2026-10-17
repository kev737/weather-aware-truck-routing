import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import type { Place } from "../api";

const places: Place[] = [
  { name: "San Antonio, Texas, US", lat: 29.42, lon: -98.49 },
  { name: "San Antonio, Florida, US", lat: 28.34, lon: -82.27 },
];
vi.mock("../api", () => ({ searchPlaces: vi.fn(async () => places) }));
const { PlaceInput } = await import("./PlaceInput");

function Harness({ onPick }: { onPick: (v: string | Place) => void }) {
  const [value, setValue] = useState<string | Place>("");
  return (
    <form onSubmit={(e) => e.preventDefault()}>
      <PlaceInput
        label="From"
        value={value}
        onChange={(v) => {
          setValue(v);
          onPick(v);
        }}
      />
      <button type="submit">Plan routes</button>
    </form>
  );
}

describe("PlaceInput", () => {
  it("suggests places as you type and picks one with the arrow keys and Enter", async () => {
    const onPick = vi.fn();
    render(<Harness onPick={onPick} />);
    await userEvent.type(screen.getByRole("combobox"), "San Anto");
    await waitFor(() => expect(screen.getAllByRole("option")).toHaveLength(2));
    await userEvent.keyboard("{ArrowDown}{Enter}");
    expect(onPick).toHaveBeenLastCalledWith(places[0]); // exact coordinates, not free text
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("Escape closes the list", async () => {
    render(<Harness onPick={() => {}} />);
    await userEvent.type(screen.getByRole("combobox"), "San Anto");
    await waitFor(() => screen.getByRole("listbox"));
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("the list sits in the page flow, so it can never cover the Plan button", async () => {
    render(<Harness onPick={() => {}} />);
    await userEvent.type(screen.getByRole("combobox"), "San Anto");
    const list = await screen.findByRole("listbox");
    expect(getComputedStyle(list).position).not.toBe("absolute");
    await userEvent.click(screen.getByRole("button", { name: "Plan routes" }));
    expect(screen.getByRole("combobox")).toHaveValue("San Anto"); // the click didn't pick a suggestion
  });
});
