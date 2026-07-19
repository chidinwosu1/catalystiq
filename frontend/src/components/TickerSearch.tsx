import { useEffect, useRef, useState } from "react";
import { Check, Search } from "lucide-react";
import { filterTickers, type Ticker } from "../lib/tickers";

interface TickerSearchProps {
  /** Called when the user confirms a stock (click, Enter, or the Select button). */
  onSelect: (symbol: string) => void;
  /** Currently selected symbol to display when the field is not being edited. */
  value?: string;
  placeholder?: string;
  /** Extra classes for the outer wrapper — use to control width. */
  className?: string;
}

/**
 * Consistent stock-search field used everywhere in the app. Clicking or typing
 * opens a searchable dropdown of supported tickers (company name + symbol);
 * supports mouse and full keyboard navigation, plus a Select button to confirm
 * the highlighted row. A typed symbol that isn't on the list can still be
 * confirmed (live data works for any valid ticker).
 */
export default function TickerSearch({
  onSelect,
  value = "",
  placeholder = "Search ticker or company…",
  className = "",
}: TickerSearchProps) {
  const [selected, setSelected] = useState(value);
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState(0);

  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const rowRefs = useRef<(HTMLLIElement | null)[]>([]);

  // Keep the displayed selection in sync when the parent changes it (e.g. after
  // navigating to a new ticker from another page).
  useEffect(() => {
    setSelected(value);
  }, [value]);

  const query = text.trim();
  const matches: Ticker[] = filterTickers(query);
  // Allow confirming an off-list symbol the user typed.
  const typedSymbol = query.toUpperCase();
  const hasExact = matches.some((t) => t.symbol === typedSymbol);
  const showTyped = query.length > 0 && !hasExact;
  const optionCount = matches.length + (showTyped ? 1 : 0);

  // Keep the highlighted row scrolled into view.
  useEffect(() => {
    if (!open) return;
    rowRefs.current[highlight]?.scrollIntoView({ block: "nearest" });
  }, [highlight, open]);

  function openDropdown() {
    setText("");
    setHighlight(0);
    setOpen(true);
  }

  function confirm(symbol: string) {
    const sym = symbol.trim().toUpperCase();
    if (!sym) return;
    setSelected(sym);
    setText("");
    setOpen(false);
    inputRef.current?.blur();
    onSelect(sym);
  }

  function confirmHighlighted() {
    if (highlight < matches.length) {
      confirm(matches[highlight].symbol);
    } else if (showTyped) {
      confirm(typedSymbol);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (!open && (e.key === "ArrowDown" || e.key === "Enter")) {
      openDropdown();
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlight((h) => Math.min(h + 1, optionCount - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => Math.max(h - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      confirmHighlighted();
    } else if (e.key === "Escape") {
      setOpen(false);
      inputRef.current?.blur();
    }
  }

  const rows: { symbol: string; name: string; typed?: boolean }[] = [
    ...matches.map((t) => ({ symbol: t.symbol, name: t.name })),
    ...(showTyped ? [{ symbol: typedSymbol, name: "Search this ticker", typed: true }] : []),
  ];

  return (
    <div className={`relative ${className}`}>
      <label className="flex items-center gap-2 rounded-lg border border-border bg-surface px-3 py-2 text-sm text-ink-muted focus-within:border-brand-blue/50">
        <Search size={15} className="shrink-0" />
        <input
          ref={inputRef}
          type="text"
          value={open ? text : selected}
          placeholder={selected && !open ? selected : placeholder}
          onFocus={openDropdown}
          onClick={openDropdown}
          onBlur={() => setOpen(false)}
          onChange={(e) => {
            setText(e.target.value);
            setHighlight(0);
            setOpen(true);
          }}
          onKeyDown={handleKeyDown}
          role="combobox"
          aria-expanded={open}
          aria-controls="ticker-search-list"
          aria-autocomplete="list"
          className="w-full bg-transparent uppercase text-ink-primary placeholder:normal-case placeholder:text-ink-muted focus:outline-none"
        />
      </label>

      {open && (
        <div className="absolute left-0 right-0 z-50 mt-1.5 overflow-hidden rounded-xl border border-border-strong bg-surface shadow-[0_20px_50px_rgba(0,0,0,0.5)]">
          <ul
            id="ticker-search-list"
            ref={listRef}
            role="listbox"
            className="max-h-64 overflow-y-auto py-1"
          >
            {rows.length === 0 && (
              <li className="px-3 py-2.5 text-sm text-ink-muted">No matching tickers</li>
            )}
            {rows.map((row, i) => {
              const active = i === highlight;
              return (
                <li
                  key={`${row.symbol}-${i}`}
                  ref={(el) => {
                    rowRefs.current[i] = el;
                  }}
                  role="option"
                  aria-selected={active}
                  // onMouseDown fires before the input's onBlur, so the click
                  // isn't cancelled by the dropdown closing.
                  onMouseDown={(e) => {
                    e.preventDefault();
                    confirm(row.symbol);
                  }}
                  onMouseEnter={() => setHighlight(i)}
                  className={`flex cursor-pointer items-center justify-between gap-3 px-3 py-2 text-sm ${
                    active ? "bg-brand-blue/15" : ""
                  }`}
                >
                  <span className="min-w-0">
                    <span className="font-semibold text-ink-primary">{row.symbol}</span>
                    <span className="ml-2 truncate text-[12.5px] text-ink-secondary">
                      {row.name}
                    </span>
                  </span>
                  {active && <Check size={14} className="shrink-0 text-[#5ea8ff]" />}
                </li>
              );
            })}
          </ul>

          <div className="flex items-center justify-between gap-2 border-t border-border px-3 py-2">
            <span className="truncate text-[11.5px] text-ink-muted">
              ↑↓ to navigate · Enter to select
            </span>
            <button
              type="button"
              disabled={optionCount === 0}
              onMouseDown={(e) => {
                e.preventDefault();
                confirmHighlighted();
              }}
              className="shrink-0 rounded-lg bg-brand-blue px-3.5 py-1.5 text-[12.5px] font-semibold text-white transition-opacity hover:bg-brand-blue/90 disabled:opacity-40"
            >
              Select
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
