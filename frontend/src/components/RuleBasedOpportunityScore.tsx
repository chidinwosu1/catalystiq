import { useEffect, useState } from "react";
import { AlertTriangle, Info, Loader2 } from "lucide-react";
import {
  ApiError,
  getOpportunityScore,
  type OpportunityFactor,
  type OpportunityScore,
} from "../lib/api";
import SectionCard from "./SectionCard";

/**
 * Rule-Based Opportunity Score (Setup Strength).
 *
 * Displays the deterministic technical setup-strength score with full
 * transparency: the band label, every factor's contribution / max / formula /
 * explanation, data freshness, and calculation + data timestamps. It is
 * explicitly NOT a probability of profit, AI confidence, or ML prediction, and
 * never a buy/sell instruction — those labels are shown to the user. Behavioral
 * and sentiment factors, and the future ML models, are shown as unavailable
 * (never fabricated).
 */

const FACTOR_LABEL: Record<string, string> = {
  trend: "Trend & market structure",
  momentum: "Momentum",
  volume_liquidity: "Volume & liquidity",
  volatility_risk: "Volatility & risk",
  market_sector: "Market & sector context",
};

function bandTone(label: string | null): string {
  switch (label) {
    case "Strong setup":
      return "text-status-good";
    case "Favorable setup":
      return "text-status-good";
    case "Weak setup":
    case "Unfavorable setup":
      return "text-status-critical";
    default:
      return "text-ink-primary";
  }
}

function FactorRow({ f }: { f: OpportunityFactor }) {
  const pct = f.score !== null ? (f.score / f.max_score) * 100 : 0;
  return (
    <div className="rounded-lg border border-border px-3 py-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium text-ink-primary">
          {FACTOR_LABEL[f.name] ?? f.name}
        </span>
        <span className="text-sm tabular-nums text-ink-secondary">
          {f.status === "available" ? `${f.score} / ${f.max_score}` : "Insufficient data"}
        </span>
      </div>
      {f.status === "available" && (
        <div className="mt-1 h-1.5 overflow-hidden rounded bg-surface-2">
          <div className="h-full rounded bg-brand-blue" style={{ width: `${pct}%` }} />
        </div>
      )}
      <p className="mt-1 text-[11px] text-ink-muted">{f.explanation}</p>
    </div>
  );
}

export default function RuleBasedOpportunityScore({ symbol }: { symbol: string }) {
  const [data, setData] = useState<OpportunityScore | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!symbol) return;
    let alive = true;
    setLoading(true);
    setError(null);
    getOpportunityScore(symbol)
      .then((d) => {
        if (alive) setData(d);
      })
      .catch((err) => {
        if (alive) setError(err instanceof ApiError ? err.message : "Could not load the score.");
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [symbol]);

  return (
    <SectionCard
      title="Rule-Based Opportunity Score"
      description="Setup Strength — deterministic, technical only. Not a probability of profit, not an AI/ML prediction, and not a buy/sell instruction."
    >
      {loading && !data && (
        <div className="flex items-center gap-2 text-sm text-ink-secondary">
          <Loader2 size={14} className="animate-spin" /> Scoring {symbol}…
        </div>
      )}

      {error && (
        <div className="flex items-start gap-2 rounded-lg border border-status-critical/40 bg-status-critical-soft px-3 py-2 text-xs text-status-critical">
          <AlertTriangle size={13} className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {data && data.status === "insufficient_data" && (
        <div className="flex items-start gap-2 rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm text-ink-secondary">
          <Info size={14} className="mt-0.5 shrink-0" />
          <span>Insufficient data — {data.reason ?? "a required factor is missing or stale."}</span>
        </div>
      )}

      {data && data.status === "available" && (
        <>
          <div className="mb-3 flex items-end justify-between gap-3">
            <div>
              <div className={`text-3xl font-bold tabular-nums ${bandTone(data.label)}`}>
                {data.score}
                <span className="text-base font-normal text-ink-muted"> / {data.max_score}</span>
              </div>
              <div className={`text-sm font-semibold ${bandTone(data.label)}`}>{data.label}</div>
            </div>
            <div className="text-right text-[11px] text-ink-muted">
              <div>Coverage {data.factor_coverage}</div>
              <div>Data {data.freshness}</div>
              {data.data_as_of && <div>As of {data.data_as_of.slice(0, 10)}</div>}
            </div>
          </div>

          <div className="space-y-2">
            {data.factors.map((f) => (
              <FactorRow key={f.name} f={f} />
            ))}
          </div>

          <p className="mt-2 text-[11px] text-ink-muted">
            "Strong setup" describes technical setup strength only — it is not an instruction to
            enter; entry still requires trade-plan, risk, and execution checks. Formula{" "}
            {data.formula_version}, calculated {new Date(data.calculated_at).toLocaleString()}.
          </p>
        </>
      )}

      {data && (
        <div className="mt-3 border-t border-border pt-3">
          <div className="text-[11px] font-medium uppercase tracking-wide text-ink-muted">
            Not yet available
          </div>
          <ul className="mt-1 space-y-0.5 text-[11px] text-ink-secondary">
            {data.unavailable_factors.map((u) => (
              <li key={u.name}>
                <span className="capitalize">{u.name}</span> — Unavailable — {u.reason.toLowerCase()}
              </li>
            ))}
            <li>ML models — {data.ml.reason}</li>
          </ul>
        </div>
      )}
    </SectionCard>
  );
}
