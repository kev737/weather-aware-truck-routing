import { useEffect, useId, useRef, useState } from "react";
import { searchPlaces, type Place } from "../api";
import { FieldError } from "./FieldError";

interface Props {
  label: string;
  value: string | Place;
  onChange: (value: string | Place) => void;
  placeholder?: string;
  error?: string; // shown under the input
}

/**
 * Text input with place suggestions. Typing gives free text; picking a suggestion
 * gives exact coordinates. The list sits in the page flow (it pushes the form down)
 * so it can never cover the Plan button; arrow keys, Enter and Escape work.
 */
export function PlaceInput({ label, value, onChange, placeholder, error }: Props) {
  const text = typeof value === "string" ? value : value.name;
  const [suggestions, setSuggestions] = useState<Place[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const picked = useRef(false);
  const typed = useRef(false); // no lookups for the prefilled values, only once the user types
  const listId = useId();
  const errorId = useId();

  useEffect(() => {
    if (!typed.current || picked.current || text.trim().length < 3) {
      picked.current = false;
      setSuggestions([]);
      return;
    }
    // Wait until typing pauses, and drop stale responses when the text changes.
    const controller = new AbortController();
    const timer = setTimeout(() => {
      searchPlaces(text, controller.signal)
        .then((places) => {
          setSuggestions(places);
          setActive(-1);
        })
        .catch(() => setSuggestions([]));
    }, 250);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [text]);

  const pick = (place: Place) => {
    picked.current = true;
    onChange(place);
    setOpen(false);
  };

  const showList = open && suggestions.length > 0;

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Escape") {
      setOpen(false);
    } else if (e.key === "ArrowDown" && suggestions.length) {
      e.preventDefault();
      setOpen(true);
      setActive((i) => (i + 1) % suggestions.length);
    } else if (e.key === "ArrowUp" && suggestions.length) {
      e.preventDefault();
      setActive((i) => (i <= 0 ? suggestions.length - 1 : i - 1));
    } else if (e.key === "Enter") {
      if (showList && active >= 0) {
        e.preventDefault(); // pick the highlighted place instead of submitting
        pick(suggestions[active]);
      } else {
        setOpen(false); // the form submits with the text as typed
      }
    }
  };

  return (
    <label className="field">
      <span>{label}</span>
      <input
        value={text}
        placeholder={placeholder}
        onChange={(e) => {
          typed.current = true;
          onChange(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={onKeyDown}
        autoComplete="off"
        role="combobox"
        aria-expanded={showList}
        aria-controls={listId}
        aria-activedescendant={active >= 0 ? `${listId}-${active}` : undefined}
        aria-invalid={Boolean(error)}
        aria-describedby={error ? errorId : undefined}
      />
      <FieldError id={errorId} message={error} />
      {showList && (
        <ul className="suggest-list" role="listbox" id={listId}>
          {suggestions.map((p, i) => (
            <li
              key={`${p.lat},${p.lon}`}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={i === active}
              className={i === active ? "active" : ""}
              onMouseDown={() => pick(p)}
            >
              {p.name}
            </li>
          ))}
        </ul>
      )}
    </label>
  );
}
