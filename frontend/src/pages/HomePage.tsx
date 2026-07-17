import {
  ArrowRight,
  BarChart3,
  Briefcase,
  LineChart,
  Wallet,
} from "lucide-react";
import Logo from "../components/Logo";
import SectionCard from "../components/SectionCard";
import { PAGES, type PageId } from "../types/nav";

interface HomePageProps {
  onNavigate: (page: PageId) => void;
}

const ICONS: Record<PageId, typeof Wallet> = {
  home: Wallet,
  trade: Wallet,
  portfolio: Briefcase,
  markets: LineChart,
  analysis: BarChart3,
};

export default function HomePage({ onNavigate }: HomePageProps) {
  return (
    <div className="space-y-8">
      <div className="flex flex-col items-center py-8 text-center">
        <Logo size="lg" />
        <p className="mt-4 max-w-xl text-sm text-ink-secondary">
          Catalyst IQ pulls real market data, runs it through a deterministic analytical
          engine, and layers a behavioral (Functional Behavioral Analysis) lens on top -
          modeling how investors as a crowd tend to react to triggers, not just what the
          price is doing. Everything is either real, backend-computed data, or clearly
          labeled as a demo of a module that isn't built yet.
        </p>
      </div>

      <SectionCard title="What's real vs. demo right now" className="mx-auto max-w-3xl">
        <ul className="space-y-2 text-sm text-ink-secondary">
          <li>
            <span className="font-medium text-ink-primary">Real:</span> quotes, price
            history, account balance, positions, and order execution (including scheduled
            and bracket orders) against the connected paper-trading broker.
          </li>
          <li>
            <span className="font-medium text-ink-primary">Demo, clearly labeled:</span>{" "}
            ratings, probability/confidence scores, sector rankings, and behavioral-analysis
            predictions - these need the analytical and Functional Behavioral Analysis
            engines from the build spec, which aren't built yet. Look for the{" "}
            <span className="rounded-full border border-status-warning/40 bg-status-warning-soft px-1.5 py-0.5 text-[10px] font-semibold uppercase text-status-warning">
              Demo data
            </span>{" "}
            badge.
          </li>
        </ul>
      </SectionCard>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {PAGES.map((page) => {
          const Icon = ICONS[page.id];
          return (
            <button
              key={page.id}
              onClick={() => onNavigate(page.id)}
              className="group flex flex-col items-start rounded-xl border border-border bg-surface p-5 text-left transition-colors hover:border-brand-blue/50"
            >
              <div className="flex w-full items-center justify-between">
                <span className="rounded-lg bg-surface-2 p-2 text-brand-blue">
                  <Icon size={18} />
                </span>
                <ArrowRight
                  size={16}
                  className="text-ink-muted transition-transform group-hover:translate-x-0.5 group-hover:text-ink-primary"
                />
              </div>
              <h3 className="mt-3 text-base font-semibold text-ink-primary">{page.label}</h3>
              <p className="mt-1 text-sm text-ink-secondary">{page.description}</p>
            </button>
          );
        })}
      </div>
    </div>
  );
}
