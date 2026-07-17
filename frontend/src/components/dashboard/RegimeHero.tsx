import { Activity, CalendarClock, Flame, Gauge, ShieldAlert, Target } from "lucide-react";
import type { ReactNode } from "react";
import DemoBadge from "../DemoBadge";
import { marketSummary } from "../../mockDashboardData";
import { regimeRole, riskRole, roleClasses } from "../../lib/theme";

function HeroStat({
  icon,
  label,
  value,
  valueClass = "text-ink-primary",
  sub,
}: {
  icon: ReactNode;
  label: string;
  value: ReactNode;
  valueClass?: string;
  sub?: string;
}) {
  return (
    <div className="flex flex-col justify-between rounded-xl border border-border bg-surface px-4 py-3">
      <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-ink-muted">
        <span className="text-ink-secondary">{icon}</span>
        {label}
      </div>
      <p className={`mt-2 text-2xl font-semibold leading-none ${valueClass}`}>{value}</p>
      {sub && <p className="mt-1.5 text-xs text-ink-secondary">{sub}</p>}
    </div>
  );
}

export default function RegimeHero() {
  const s = marketSummary;
  const regime = roleClasses[regimeRole(s.regime)];
  const risk = roleClasses[riskRole(s.risk)];

  return (
    <section className="overflow-hidden rounded-2xl border border-border bg-gradient-to-b from-surface-2/60 to-surface">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-5 py-3">
        <div className="flex items-center gap-2">
          <Activity size={16} className="text-brand-blue" />
          <h1 className="text-sm font-semibold uppercase tracking-[0.14em] text-ink-primary">
            Market Command Center
          </h1>
          <span className="text-xs text-ink-muted">· {s.asOf}</span>
        </div>
        <DemoBadge />
      </div>

      <div className="grid grid-cols-2 gap-3 p-5 md:grid-cols-3 lg:grid-cols-6">
        {/* Regime — the emphasized tile */}
        <div
          className={`col-span-2 flex flex-col justify-between rounded-xl border p-4 lg:col-span-2 ${regime.border} ${regime.bg}`}
        >
          <p className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-ink-secondary">
            <Gauge size={13} /> Market Regime
          </p>
          <p className={`mt-2 text-4xl font-bold leading-none ${regime.text}`}>{s.regime}</p>
          <p className="mt-2 text-xs text-ink-secondary">{s.regimeDetail}</p>
        </div>

        <HeroStat
          icon={<Gauge size={13} />}
          label="Confidence"
          value={`${s.confidence}%`}
          valueClass="text-brand-blue"
          sub="Model conviction"
        />
        <HeroStat
          icon={<ShieldAlert size={13} />}
          label="Overall Risk"
          value={s.risk}
          valueClass={risk.text}
          sub="Regime + volatility"
        />
        <HeroStat
          icon={<Target size={13} />}
          label="High-Conviction"
          value={s.highConvictionCount}
          sub="Opportunities today"
        />
        <HeroStat
          icon={<CalendarClock size={13} />}
          label="Major Catalysts"
          value={s.majorCatalystCount}
          sub="Scheduled today"
        />
      </div>

      {s.portfolioAlertCount > 0 && (
        <div className="flex items-center gap-2 border-t border-border px-5 py-2.5 text-xs text-status-warning">
          <Flame size={13} />
          <span>
            {s.portfolioAlertCount} portfolio alert{s.portfolioAlertCount === 1 ? "" : "s"} need
            your attention — see below.
          </span>
        </div>
      )}
    </section>
  );
}
