import { useEffect, type ReactNode } from "react";
import {
  Activity,
  BarChart3,
  Check,
  CircleAlert,
  Crosshair,
  Globe,
  Maximize2,
  Minimize2,
  Send,
  Shield,
  TrendingUp,
  X,
  Zap,
} from "lucide-react";
import RatingBadge from "../RatingBadge";
import { riskRole, roleClasses } from "../../lib/theme";
import type { OpportunityDetail } from "../../mockTradeCenter";

interface OpportunityPanelProps {
  opp: OpportunityDetail | null;
  livePrice?: number | null;
  expanded: boolean;
  onClose: () => void;
  onToggleExpand: () => void;
  onTrade: (symbol: string) => void;
  onAnalyze: (symbol: string) => void;
}

function PCard({
  title,
  icon,
  action,
  children,
}: {
  title: string;
  icon: ReactNode;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="mb-3.5 break-inside-avoid rounded-2xl border border-border bg-white/[0.025] p-4">
      <div className="mb-2.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-muted">
        <span className="text-[#5ea8ff]">{icon}</span>
        {title}
        {action && <span className="ml-auto">{action}</span>}
      </div>
      {children}
    </div>
  );
}

function EvidenceList({ items }: { items: string[] }) {
  return (
    <ul className="flex flex-col gap-2">
      {items.map((t) => (
        <li key={t} className="flex gap-2.5 text-[13px] text-ink-secondary">
          <Check size={15} className="mt-0.5 shrink-0 text-[#5ea8ff]" />
          <span>{t}</span>
        </li>
      ))}
    </ul>
  );
}

export default function OpportunityPanel({
  opp,
  livePrice,
  expanded,
  onClose,
  onToggleExpand,
  onTrade,
  onAnalyze,
}: OpportunityPanelProps) {
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const open = opp !== null;
  const risk = opp ? roleClasses[riskRole(opp.risk)] : null;

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
        className={`cq-panel z-[90] flex flex-col border-l border-border-strong bg-surface/95 shadow-[-30px_0_80px_rgba(0,0,0,0.5)] backdrop-blur-2xl ${
          open ? "is-open" : ""
        } ${expanded ? "is-full" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={opp ? `${opp.symbol} opportunity` : "Opportunity"}
        aria-hidden={!open}
      >
        {opp && (
          <>
            <div className="flex items-start gap-3 border-b border-border px-5 py-4">
              <div>
                <div className="flex items-center gap-2.5">
                  <span className="text-[22px] font-bold tracking-tight text-ink-primary">
                    {opp.symbol}
                  </span>
                  <RatingBadge rating={opp.rating} />
                </div>
                <div className="mt-0.5 text-[12.5px] text-ink-muted">{opp.companyName}</div>
                <div className="mt-1.5 font-mono text-[13px] text-ink-secondary">
                  {livePrice != null
                    ? `${livePrice.toLocaleString("en-US", { style: "currency", currency: "USD" })} · live`
                    : opp.price}
                </div>
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
              <div className={expanded ? "mx-auto max-w-4xl lg:[column-count:2] lg:[column-gap:16px]" : ""}>
                <PCard
                  title="Opportunity summary"
                  icon={<CircleAlert size={14} />}
                  action={<RatingBadge rating={opp.rating} />}
                >
                  <p className="text-[13.5px] leading-relaxed text-ink-secondary">{opp.summary}</p>
                </PCard>

                <PCard title="Scores" icon={<TrendingUp size={14} />}>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <div className="text-[11px] text-ink-muted">Opportunity</div>
                      <div className="font-mono text-[26px] font-bold text-[#5ea8ff]">
                        {opp.catalystScore}
                      </div>
                      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface-2">
                        <span
                          className="block h-full rounded-full bg-brand-blue"
                          style={{ width: `${opp.catalystScore}%` }}
                        />
                      </div>
                    </div>
                    <div>
                      <div className="text-[11px] text-ink-muted">Confidence</div>
                      <div className="font-mono text-[26px] font-bold text-status-good">
                        {opp.confidence}
                      </div>
                      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface-2">
                        <span
                          className="block h-full rounded-full bg-status-good"
                          style={{ width: `${opp.confidence}%` }}
                        />
                      </div>
                    </div>
                  </div>
                </PCard>

                <PCard title="Suggested strategy" icon={<Send size={14} />}>
                  <p className="text-[13.5px] leading-relaxed text-ink-secondary">{opp.strategy}</p>
                </PCard>

                <PCard title="Technical summary" icon={<BarChart3 size={14} />}>
                  <p className="text-[13.5px] leading-relaxed text-ink-secondary">{opp.technical}</p>
                </PCard>

                <PCard title="Market context" icon={<Globe size={14} />}>
                  <p className="text-[13.5px] leading-relaxed text-ink-secondary">{opp.market}</p>
                </PCard>

                <PCard
                  title="Risk assessment"
                  icon={<Shield size={14} />}
                  action={
                    <span className={`text-[12px] font-bold ${risk?.text}`}>{opp.risk}</span>
                  }
                >
                  <p className="text-[13.5px] leading-relaxed text-ink-secondary">{opp.riskText}</p>
                </PCard>

                <PCard title="Supporting evidence" icon={<Activity size={14} />}>
                  <EvidenceList items={opp.evidence} />
                </PCard>

                <PCard title="Key catalysts" icon={<Zap size={14} />}>
                  <EvidenceList items={opp.catalysts} />
                </PCard>

                <PCard title="Trade plan" icon={<Crosshair size={14} />}>
                  <div className="grid grid-cols-2 gap-2.5">
                    <div className="rounded-xl border border-border px-3 py-2.5">
                      <div className="text-[10px] uppercase tracking-wide text-ink-muted">
                        Suggested entry
                      </div>
                      <div className="mt-0.5 font-mono text-[14px] font-semibold text-[#5ea8ff]">
                        {opp.entry}
                      </div>
                    </div>
                    <div className="rounded-xl border border-border px-3 py-2.5">
                      <div className="text-[10px] uppercase tracking-wide text-ink-muted">
                        Profit target
                      </div>
                      <div className="mt-0.5 font-mono text-[14px] font-semibold text-status-good">
                        {opp.target}
                      </div>
                    </div>
                    <div className="rounded-xl border border-border px-3 py-2.5">
                      <div className="text-[10px] uppercase tracking-wide text-ink-muted">
                        Stop loss
                      </div>
                      <div className="mt-0.5 font-mono text-[14px] font-semibold text-status-critical">
                        {opp.stop}
                      </div>
                    </div>
                    <div className="rounded-xl border border-border px-3 py-2.5">
                      <div className="text-[10px] uppercase tracking-wide text-ink-muted">
                        Suggested exit
                      </div>
                      <div className="mt-0.5 font-mono text-[14px] font-semibold text-ink-primary">
                        {opp.exit}
                      </div>
                    </div>
                  </div>
                </PCard>
              </div>
            </div>

            <div className="flex gap-2.5 border-t border-border px-5 py-3.5">
              <button
                onClick={() => onAnalyze(opp.symbol)}
                className="rounded-xl border border-border-strong px-4 py-2.5 text-[13px] font-semibold text-ink-secondary transition-colors hover:text-ink-primary"
              >
                Full analysis
              </button>
              <button
                onClick={() => onTrade(opp.symbol)}
                className="flex flex-1 items-center justify-center gap-2 rounded-xl bg-brand-blue px-4 py-2.5 text-[13px] font-semibold text-white transition-colors hover:bg-brand-blue/90"
              >
                Trade {opp.symbol}
                <Send size={15} />
              </button>
            </div>
          </>
        )}
      </aside>
    </>
  );
}
