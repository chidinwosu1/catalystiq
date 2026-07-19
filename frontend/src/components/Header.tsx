import { LogOut } from "lucide-react";
import Logo from "./Logo";
import TickerSearch from "./TickerSearch";
import { PAGES, type PageId } from "../types/nav";

interface HeaderProps {
  activePage: PageId;
  onNavigate: (page: PageId) => void;
  onSearch: (symbol: string) => void;
  onSignOut?: () => void;
}

export default function Header({ activePage, onNavigate, onSearch, onSignOut }: HeaderProps) {
  // The primary navigation is hidden on the landing (home) page so it reads as a
  // clean entry point; it stays available in the header on every other page.
  const showNav = activePage !== "home";

  return (
    <header className="relative z-50 border-b border-border bg-page/80 backdrop-blur">
      <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-6 py-4">
        <button onClick={() => onNavigate("home")} aria-label="Go to home">
          <Logo size="md" />
        </button>

        {showNav && (
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
        )}

        <div className="flex items-center gap-2">
          <TickerSearch
            onSelect={onSearch}
            placeholder="Search ticker or company…"
            className="w-56"
          />
          {onSignOut && (
            <button
              onClick={onSignOut}
              title="Sign out"
              aria-label="Sign out"
              className="rounded-lg border border-border p-2 text-ink-secondary hover:text-ink-primary"
            >
              <LogOut size={15} />
            </button>
          )}
        </div>
      </div>

      {showNav && (
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
      )}
    </header>
  );
}
