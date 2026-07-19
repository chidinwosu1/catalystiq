import { useEffect } from "react";
import { Maximize2, Minimize2, TrendingDown, TrendingUp, X } from "lucide-react";
import { catalysts, marketOverview, sectorRotation } from "../../mockMarketData";

interface MarketOverviewPanelProps {
  open: boolean;
  expanded: boolean;
  onClose: () => void;
  onToggleExpand: () => void;
}

const STATUS_CLASS: Record<string, string> = {
  Confirmed: "border-status-good/40 bg-status-good-soft text-status-good",
  Proposed: "border-status-warning/40 bg-status-warning-soft text-status-warning",
  Speculation: "border-status-neutral/40 bg-status-neutral-soft text-status-neutral",
};

export default function MarketOverviewPanel({
  open,
  expanded,
  onClose,
  onToggleExpand,
}: MarketOverviewPanelProps) {
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const topSectors = [...sectorRotation].sort((a, b) => b.leadershipScore - a.leadershipScore);

  return (
    <>
      <div
        className={`cq-backdrop fixed inset-0 z-[80] bg-[rgba(5,8,13,0.6)] backdrop-blur-[2px] ${
          open ? "is-open" : ""
        }`}
        onClick={onClose}
        aria-hidden="true"
      />
      <aside
        className={`cq-panel cq-left z-[90] flex flex-col border-r border-border-strong bg-surface/95 shadow-[30px_0_80px_rgba(0,0,0,0.5)] backdrop-blur-2xl ${
          open ? "is-open" : ""
        } ${expanded ? "is-full" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label="Market overview"
        aria-hidden={!open}
      >
        <div className="flex items-start gap-3 border-b border-border px-5 py-4">
          <div>
            <div className="font-mono text-xs font-semibold uppercase tracking-[0.18em] text-[#5ea8ff]">
              Market Overview
            </div>
            <div className="mt-1 text-[18px] font-bold tracking-tight text-ink-primary">
              Today's macro picture
            </div>
            <p className="mt-1 max-w-[52ch] text-[12.5px] text-ink-secondary">
              Moderately bullish regime, Technology leading. Rising yields pressuring
              long-duration growth; volatility contained.
            </p>
          </div>
          <div className="ml-auto flex gap-1.5">
            <button
              onClick={onToggleExpand}
              title={expanded ? "Collapse" : "Expand to full screen"}
              aria-label={expanded ? "Collapse panel" : "Expand panel"}
              className="grid h-[34px] w-[34px] place-items-center rounded-[10px] border border-border bg-surface text-ink-secondary hover:border-border-strong hover:text-ink-primary"
            >
              {expanded ? <Minimize2 size={17} /> : <Maximize2 size={17} />}
            </button>
            <button
              onClick={onClose}
              title="Close"
              aria-label="Close panel"
              className="grid h-[34px] w-[34px] place-items-center rounded-[10px] border border-border bg-surface text-ink-secondary hover:border-border-strong hover:text-ink-primary"
            >
              <X size={17} />
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          <div className={expanded ? "mx-auto grid max-w-4xl gap-4 lg:grid-cols-2" : "space-y-4"}>
            {/* Indices */}
            <section>
              <h3 className="mb-2.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-muted">
                Index &amp; rates
              </h3>
              <div className="grid grid-cols-2 gap-2">
                {marketOverview.map((row) => {
                  const up = row.changePct >= 0;
                  return (
                    <div key={row.symbol} className="rounded-xl border border-border bg-white/[0.02] px-3 py-2">
                      <div className="flex items-center justify-between">
                        <span className="text-[12px] font-medium text-ink-primary">{row.label}</span>
                        <span
                          className={`flex items-center gap-0.5 text-[11.5px] font-semibold ${
                            up ? "text-status-good" : "text-status-critical"
                          }`}
                        >
                          {up ? <TrendingUp size={11} /> : <TrendingDown size={11} />}
                          {up ? "+" : ""}
                          {row.changePct.toFixed(1)}%
                        </span>
                      </div>
                      <div className="mt-0.5 font-mono text-[14px] font-semibold text-ink-primary">
                        {row.level}
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>

            {/* Sectors */}
            <section>
              <h3 className="mb-2.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-muted">
                Sector leadership
              </h3>
              <div className="space-y-1.5">
                {topSectors.slice(0, 6).map((s) => (
                  <div
                    key={s.name}
                    className="flex items-center gap-3 rounded-xl border border-border bg-white/[0.02] px-3 py-2"
                  >
                    <span className="flex-1 text-[13px] font-medium text-ink-primary">{s.name}</span>
                    <span
                      className={`w-12 text-right text-[12px] ${
                        s.weeklyPct >= 0 ? "text-status-good" : "text-status-critical"
                      }`}
                    >
                      {s.weeklyPct >= 0 ? "+" : ""}
                      {s.weeklyPct.toFixed(1)}%
                    </span>
                    <div className="h-1.5 w-20 overflow-hidden rounded-full bg-surface-2">
                      <span
                        className="block h-full rounded-full bg-brand-blue"
                        style={{ width: `${s.leadershipScore}%` }}
                      />
                    </div>
                    <span className="w-7 text-right font-mono text-[12px] font-semibold text-ink-primary">
                      {s.leadershipScore}
                    </span>
                  </div>
                ))}
              </div>
            </section>

            {/* Catalysts */}
            <section className={expanded ? "lg:col-span-2" : ""}>
              <h3 className="mb-2.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-muted">
                Economic &amp; political catalysts
              </h3>
              <ul className="space-y-2">
                {catalysts.map((c) => (
                  <li key={c.headline} className="rounded-xl border border-border bg-white/[0.02] px-3 py-2.5">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-[10.5px] font-medium uppercase tracking-wide text-ink-muted">
                        {c.category}
                      </span>
                      <span
                        className={`rounded-full border px-2 py-0.5 text-[9.5px] font-semibold uppercase tracking-wide ${STATUS_CLASS[c.status]}`}
                      >
                        {c.status}
                      </span>
                    </div>
                    <p className="mt-1 text-[13px] font-medium text-ink-primary">{c.headline}</p>
                    <p className="mt-0.5 text-[12px] text-ink-secondary">{c.transmissionPath}</p>
                  </li>
                ))}
              </ul>
            </section>
          </div>
        </div>
      </aside>
    </>
  );
}
