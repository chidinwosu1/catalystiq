import { useState } from "react";
import { Search } from "lucide-react";
import Logo from "./Logo";
import { PAGES, type PageId } from "../types/nav";

interface HeaderProps {
  activePage: PageId;
  onNavigate: (page: PageId) => void;
  onSearch: (symbol: string) => void;
}

export default function Header({ activePage, onNavigate, onSearch }: HeaderProps) {
  const [value, setValue] = useState("");

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key !== "Enter") return;
    const symbol = value.trim().toUpperCase();
    if (!symbol) return;
    onSearch(symbol);
  }

  return (
    <header className="border-b border-border bg-page/80 backdrop-blur">
      <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-6 py-4">
        <Logo size="md" />

        <nav className="hidden items-center gap-1 md:flex" role="tablist" aria-label="Sections">
          {PAGES.map((page) => (
            <button
              key={page.id}
              role="tab"
              aria-selected={activePage === page.id}
              onClick={() => onNavigate(page.id)}
              className={`rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
                activePage === page.id
                  ? "bg-surface-2 text-ink-primary"
                  : "text-ink-secondary hover:text-ink-primary"
              }`}
            >
              {page.label}
            </button>
          ))}
        </nav>

        <label className="flex w-56 items-center gap-2 rounded-lg border border-border bg-surface px-3 py-2 text-sm text-ink-muted focus-within:border-brand-blue/50">
          <Search size={15} />
          <input
            type="text"
            placeholder="Search ticker, press Enter…"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={handleKeyDown}
            className="w-full bg-transparent text-ink-primary placeholder:text-ink-muted focus:outline-none"
          />
        </label>
      </div>

      <nav
        className="flex items-center gap-1 overflow-x-auto border-t border-border px-4 py-2 md:hidden"
        role="tablist"
        aria-label="Sections"
      >
        {PAGES.map((page) => (
          <button
            key={page.id}
            role="tab"
            aria-selected={activePage === page.id}
            onClick={() => onNavigate(page.id)}
            className={`shrink-0 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
              activePage === page.id
                ? "bg-surface-2 text-ink-primary"
                : "text-ink-secondary hover:text-ink-primary"
            }`}
          >
            {page.label}
          </button>
        ))}
      </nav>
    </header>
  );
}
