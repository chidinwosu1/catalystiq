import { Search } from "lucide-react";
import Logo from "./Logo";

export default function Header() {
  return (
    <header className="border-b border-border bg-page/80 backdrop-blur">
      <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-6 py-4">
        <Logo size="md" />

        <nav className="hidden items-center gap-6 text-sm font-medium text-ink-secondary md:flex">
          <a href="#" className="text-ink-primary">
            Dashboard
          </a>
          <a href="#" className="transition-colors hover:text-ink-primary">
            Watchlist
          </a>
          <a href="#" className="transition-colors hover:text-ink-primary">
            Paper Trading
          </a>
        </nav>

        <label className="flex w-56 items-center gap-2 rounded-lg border border-border bg-surface px-3 py-2 text-sm text-ink-muted focus-within:border-brand-blue/50">
          <Search size={15} />
          <input
            type="text"
            placeholder="Search ticker…"
            className="w-full bg-transparent text-ink-primary placeholder:text-ink-muted focus:outline-none"
          />
        </label>
      </div>
    </header>
  );
}
