import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, BellRing, GraduationCap, Info, Loader2 } from "lucide-react";
import { getPositions, type Position } from "../../lib/api";
import DemoBadge from "../DemoBadge";
import {
  gettingStartedGuidance,
  type AlertSeverity,
  type PortfolioAlert,
} from "../../mockDashboardData";
import { roleClasses } from "../../lib/theme";

const SEVERITY_ROLE: Record<AlertSeverity, keyof typeof roleClasses> = {
  critical: "danger",
  warning: "warning",
  info: "success",
};

/**
 * Alerts derived from *real* broker positions (price action, concentration).
 * When the analytical engine lands, catalyst-score-decline and
 * earnings-tomorrow alerts join these from the same list — hence the demo tag.
 */
function deriveAlerts(positions: Position[]): PortfolioAlert[] {
  const total = positions.reduce((sum, p) => sum + Number(p.market_value), 0);
  const alerts: PortfolioAlert[] = [];

  for (const p of positions) {
    const changeToday = Number(p.change_today);
    const plpc = Number(p.unrealized_plpc);
    if (changeToday <= -0.04) {
      alerts.push({
        severity: "critical",
        symbol: p.symbol,
        title: `${p.symbol} is down ${Math.abs(changeToday * 100).toFixed(1)}% today`,
        detail: "Sharp intraday move — check whether it's approaching your stop.",
      });
    } else if (plpc <= -0.08) {
      alerts.push({
        severity: "warning",
        symbol: p.symbol,
        title: `${p.symbol} is ${Math.abs(plpc * 100).toFixed(1)}% underwater`,
        detail: "Position is drawing down against your average entry.",
      });
    }
    if (total > 0 && Number(p.market_value) / total >= 0.35) {
      alerts.push({
        severity: "warning",
        symbol: p.symbol,
        title: `Concentration: ${p.symbol} is ${((Number(p.market_value) / total) * 100).toFixed(0)}% of your book`,
        detail: "A single-name position this large raises portfolio risk.",
      });
    }
  }

  if (alerts.length === 0) {
    alerts.push({
      severity: "info",
      title: "No risk flags on your open positions",
      detail: "Prices and concentration look steady. Catalyst-score alerts arrive with the engine.",
    });
  }
  return alerts.slice(0, 5);
}

export default function PortfolioAlerts() {
  const [positions, setPositions] = useState<Position[] | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    getPositions()
      .then((p) => !cancelled && setPositions(p))
      .catch(() => !cancelled && setPositions([]))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  const alerts = useMemo(
    () => (positions && positions.length > 0 ? deriveAlerts(positions) : []),
    [positions]
  );

  const hasPositions = positions !== null && positions.length > 0;

  return (
    <section className="rounded-xl border border-border bg-surface p-5">
      <div className="mb-4 flex items-start justify-between gap-3">
        <h2 className="flex items-center gap-2 text-base font-semibold text-ink-primary">
          <BellRing size={16} className="text-brand-blue" />
          Portfolio Alerts
        </h2>
        {hasPositions && <DemoBadge />}
      </div>

      {loading && (
        <div className="flex items-center gap-2 text-sm text-ink-secondary">
          <Loader2 size={14} className="animate-spin" /> Checking your positions…
        </div>
      )}

      {!loading && hasPositions && (
        <ul className="space-y-2">
          {alerts.map((a, i) => {
            const role = roleClasses[SEVERITY_ROLE[a.severity]];
            return (
              <li
                key={`${a.title}-${i}`}
                className={`flex items-start gap-2.5 rounded-lg border px-3 py-2.5 ${role.border} ${role.bg}`}
              >
                {a.severity === "info" ? (
                  <Info size={15} className={`mt-0.5 shrink-0 ${role.text}`} />
                ) : (
                  <AlertTriangle size={15} className={`mt-0.5 shrink-0 ${role.text}`} />
                )}
                <div>
                  <p className={`text-sm font-medium ${role.text}`}>{a.title}</p>
                  <p className="mt-0.5 text-xs text-ink-secondary">{a.detail}</p>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {!loading && !hasPositions && (
        <div>
          <div className="mb-3 flex items-center gap-2 text-sm text-ink-secondary">
            <GraduationCap size={15} className="text-brand-blue" />
            You have no open positions yet — here's how to get started.
          </div>
          <ol className="space-y-2">
            {gettingStartedGuidance.map((g, i) => (
              <li key={g.title} className="flex items-start gap-3 rounded-lg border border-border px-3 py-2.5">
                <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-brand-blue/15 text-[11px] font-semibold text-brand-blue">
                  {i + 1}
                </span>
                <div>
                  <p className="text-sm font-medium text-ink-primary">{g.title}</p>
                  <p className="mt-0.5 text-xs text-ink-secondary">{g.detail}</p>
                </div>
              </li>
            ))}
          </ol>
        </div>
      )}
    </section>
  );
}
