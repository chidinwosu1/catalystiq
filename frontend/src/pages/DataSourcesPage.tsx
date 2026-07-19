import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2, RefreshCw, XCircle } from "lucide-react";
import { ApiError, getDataSourcesHealth, type DataSourceHealth } from "../lib/api";
import SectionCard from "../components/SectionCard";
import RuleBasedMacroContext from "../components/RuleBasedMacroContext";

const DOMAIN_LABEL: Record<string, string> = {
  market_data: "Market data",
  fundamentals: "Fundamentals",
  macro: "Macro",
  calendars: "Calendars",
  regulatory: "Regulatory",
  brokerage: "Brokerage",
  news: "News",
};

function timeAgo(iso: string | null): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  const diff = Date.now() - then;
  if (diff < 0) return new Date(iso).toLocaleString();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function StatusPill({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${
        ok
          ? "bg-status-good-soft text-status-good"
          : "bg-surface-2 text-ink-muted"
      }`}
    >
      {ok ? <CheckCircle2 size={11} /> : <XCircle size={11} />}
      {label}
    </span>
  );
}

export default function DataSourcesPage() {
  const [rows, setRows] = useState<DataSourceHealth[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  function load() {
    setLoading(true);
    setError(null);
    getDataSourcesHealth()
      .then((data) => setRows(data))
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : "Could not load data-source health.")
      )
      .finally(() => setLoading(false));
  }

  useEffect(load, []);

  const byDomain = useMemo(() => {
    const groups: Record<string, DataSourceHealth[]> = {};
    for (const r of rows) (groups[r.domain] ??= []).push(r);
    return Object.entries(groups).sort(([a], [b]) => a.localeCompare(b));
  }, [rows]);

  return (
    <div className="mx-auto max-w-4xl space-y-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-ink-primary">Data Sources</h1>
          <p className="mt-1 text-sm text-ink-secondary">
            Every integrated source, its enabled/configured state, last ingestion, failures, and
            freshness. No secrets are shown - only which settings are missing.
          </p>
        </div>
        <button
          onClick={load}
          className="flex shrink-0 items-center gap-1.5 rounded-lg border border-border px-3 py-2 text-xs font-medium text-ink-secondary hover:text-ink-primary"
        >
          <RefreshCw size={13} className={loading ? "animate-spin" : ""} />
          Refresh
        </button>
      </div>

      {loading && rows.length === 0 && (
        <div className="flex items-center gap-2 text-sm text-ink-secondary">
          <Loader2 size={14} className="animate-spin" /> Loading health…
        </div>
      )}

      {error && (
        <div className="flex items-start gap-2 rounded-lg border border-status-critical/40 bg-status-critical-soft px-3 py-2 text-xs text-status-critical">
          <AlertTriangle size={13} className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      <RuleBasedMacroContext />

      {byDomain.map(([domain, sources]) => (
        <SectionCard key={domain} title={DOMAIN_LABEL[domain] ?? domain}>
          <div className="space-y-2">
            {sources.map((s) => (
              <div
                key={s.name}
                className="rounded-lg border border-border px-3 py-2.5"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-ink-primary">{s.name}</span>
                    {!s.implemented && (
                      <span className="rounded-full bg-surface-2 px-2 py-0.5 text-[11px] text-ink-muted">
                        not implemented
                      </span>
                    )}
                  </div>
                  <div className="flex flex-wrap items-center gap-1.5">
                    <StatusPill ok={s.enabled} label={s.enabled ? "enabled" : "disabled"} />
                    <StatusPill
                      ok={s.configured}
                      label={s.configured ? "configured" : "not configured"}
                    />
                  </div>
                </div>

                <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-ink-secondary sm:grid-cols-3">
                  <div>
                    <span className="text-ink-muted">Last ingest</span>
                    <div className="text-ink-primary">{timeAgo(s.last_successful_ingestion_at)}</div>
                  </div>
                  <div>
                    <span className="text-ink-muted">Freshness</span>
                    <div className="text-ink-primary">{timeAgo(s.data_freshness_at)}</div>
                  </div>
                  <div>
                    <span className="text-ink-muted">Last failure</span>
                    <div className={s.last_failure_category ? "text-status-critical" : "text-ink-primary"}>
                      {s.last_failure_category ?? "none"}
                    </div>
                  </div>
                </div>

                {s.missing_settings.length > 0 && (
                  <p className="mt-2 text-[11px] text-status-warning">
                    Missing config: {s.missing_settings.join(", ")}
                  </p>
                )}

                {s.ephemeral && (
                  <p className="mt-2 text-[11px] text-ink-muted">
                    {s.note ?? "Ephemeral (no-store): fetched on demand, never persisted."}
                  </p>
                )}
              </div>
            ))}
          </div>
        </SectionCard>
      ))}
    </div>
  );
}
