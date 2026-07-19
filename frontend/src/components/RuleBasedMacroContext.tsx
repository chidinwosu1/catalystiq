import { useEffect, useState } from "react";
import { AlertTriangle, Info, Loader2, RefreshCw } from "lucide-react";
import { ApiError, getFredContext, type MacroContext, type MacroIndicator } from "../lib/api";
import SectionCard from "./SectionCard";

/**
 * The "Rule-Based Macroeconomic Context" panel.
 *
 * Displays a fixed, reviewed allowlist of public-domain FRED indicators,
 * ephemerally, each with its required attribution ("Source: … via FRED") and
 * the mandated FRED notice. Values are shown as-is (deterministic, rule-based)
 * — no score, ranking, or recommendation is derived from them, and nothing is
 * persisted (the API serves it Cache-Control: no-store).
 */

function fmt(value: number | undefined | null, units?: string): string {
  if (value === undefined || value === null) return "—";
  const n = value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return units && units.trim() === "Percent" ? `${n}%` : n;
}

function IndicatorCard({ ind }: { ind: MacroIndicator }) {
  const change =
    ind.change !== undefined && ind.change !== null
      ? `${ind.change > 0 ? "+" : ""}${ind.change.toLocaleString(undefined, {
          maximumFractionDigits: 2,
        })}`
      : null;

  return (
    <div className="rounded-lg border border-border px-3 py-2.5">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="text-sm font-medium text-ink-primary">{ind.title}</div>
          <div className="text-[11px] text-ink-muted">
            {ind.series_id} · {ind.frequency}
          </div>
        </div>
        <div className="text-right">
          {ind.status === "ok" ? (
            <>
              <div className="text-lg font-semibold tabular-nums text-ink-primary">
                {fmt(ind.latest_value, ind.units)}
              </div>
              {change && (
                <div className="text-[11px] tabular-nums text-ink-secondary">
                  {change} vs prior
                </div>
              )}
            </>
          ) : ind.status === "unavailable" ? (
            <div className="text-xs text-status-warning">temporarily unavailable</div>
          ) : (
            <div className="text-xs text-ink-muted">no data</div>
          )}
        </div>
      </div>

      <div className="mt-2 flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <span className="text-[11px] text-ink-muted">
          {ind.units}
          {ind.latest_date ? ` · as of ${ind.latest_date}` : ""}
        </span>
        {/* Required attribution — never remove or obscure. */}
        <span className="text-[11px] italic text-ink-secondary">{ind.attribution}</span>
      </div>
    </div>
  );
}

export default function RuleBasedMacroContext() {
  const [ctx, setCtx] = useState<MacroContext | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  function load() {
    setLoading(true);
    setError(null);
    getFredContext()
      .then(setCtx)
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : "Could not load macro context.")
      )
      .finally(() => setLoading(false));
  }

  useEffect(load, []);

  return (
    <SectionCard
      title="Rule-Based Macroeconomic Context"
      description="Informational macro backdrop only — not investment advice, and not used for any score, recommendation, or order."
      action={
        <button
          onClick={load}
          className="flex shrink-0 items-center gap-1.5 rounded-lg border border-border px-3 py-2 text-xs font-medium text-ink-secondary hover:text-ink-primary"
        >
          <RefreshCw size={13} className={loading ? "animate-spin" : ""} />
          Refresh
        </button>
      }
    >
      {/* Mandated FRED notice — shown on every screen that displays FRED data. */}
      <div className="mb-3 flex items-start gap-2 rounded-lg border border-border bg-surface-2 px-3 py-2 text-[11px] text-ink-secondary">
        <Info size={13} className="mt-0.5 shrink-0" />
        <span>
          {ctx?.notice ??
            "This product uses the FRED® API but is not endorsed or certified by the Federal Reserve Bank of St. Louis."}
        </span>
      </div>

      {loading && !ctx && (
        <div className="flex items-center gap-2 text-sm text-ink-secondary">
          <Loader2 size={14} className="animate-spin" /> Loading macro context…
        </div>
      )}

      {error && (
        <div className="flex items-start gap-2 rounded-lg border border-status-critical/40 bg-status-critical-soft px-3 py-2 text-xs text-status-critical">
          <AlertTriangle size={13} className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {ctx && !ctx.available && (
        <p className="text-xs text-ink-secondary">
          {ctx.reason ??
            "FRED is disabled or not configured. This optional panel is off and the rest of the app is unaffected."}
        </p>
      )}

      {ctx && ctx.available && (
        <>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {ctx.indicators.map((ind) => (
              <IndicatorCard key={ind.series_id} ind={ind} />
            ))}
          </div>
          <p className="mt-3 text-[11px] text-ink-muted">
            Ephemeral — fetched on demand, not stored or cached. Terms reviewed{" "}
            {ctx.terms_reviewed_date}
            {ctx.terms_reviewed_url && (
              <>
                {" · "}
                <a
                  href={ctx.terms_reviewed_url}
                  target="_blank"
                  rel="noreferrer"
                  className="underline hover:text-ink-secondary"
                >
                  FRED API terms
                </a>
              </>
            )}
            .
          </p>
        </>
      )}
    </SectionCard>
  );
}
