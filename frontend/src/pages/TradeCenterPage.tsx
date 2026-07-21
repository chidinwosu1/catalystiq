import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Info, Loader2 } from "lucide-react";
import WorkflowBar from "../components/trade/WorkflowBar";
import {
  ApiError,
  getOpportunityScan,
  getOpportunityScanShared,
  type EntryQualityScore,
  type OpportunityScan,
  type OpportunityScore,
} from "../lib/api";
import { useLiveQuotes } from "../lib/liveData";
import type { PageId } from "../types/nav";

interface TradeCenterPageProps {
  onTrade: (symbol: string) => void;
  onViewAnalysis: (symbol: string) => void;
  onNavigate: (page: PageId) => void;
}

const FACTOR_LABEL: Record<string, string> = {
  trend: "Trend",
  momentum: "Momentum",
  volume_liquidity: "Volume/Liq.",
  volatility_risk: "Volatility",
  market_sector: "Mkt/Sector",
};

function bandTone(label: string | null): string {
  if (label === "Strong setup" || label === "Favorable setup") return "text-status-good";
  if (label === "Weak setup" || label === "Unfavorable setup") return "text-status-critical";
  return "text-ink-primary";
}

// Entry Quality is INDEPENDENT of Setup Strength: a strong setup can still be a
// poor entry when it's extended after a large morning move.
function entryTone(rating: string | null): string {
  if (rating === "Excellent Entry" || rating === "Good Entry") return "text-status-good";
  if (rating === "Poor Entry" || rating === "Caution") return "text-status-critical";
  return "text-ink-primary";
}

// Real-time, intraday Entry Quality shown ON EACH CARD alongside Setup Strength
// so the user can tell a strong *name* from a good *moment* to enter. When there
// is no intraday feed the score is honestly "—" (insufficient_data), never a
// fabricated number.
function EntryQualityRow({ eq }: { eq: EntryQualityScore | null }) {
  const available = eq != null && eq.status === "available" && eq.score !== null;
  return (
    <div className="mt-2.5 flex items-center justify-between rounded-xl border border-border bg-surface-2/60 px-3 py-2">
      <div>
        <div className="text-[9.5px] font-semibold uppercase tracking-[0.12em] text-ink-muted">
          Entry Quality
        </div>
        <div
          className={`text-[11px] font-semibold ${
            available ? entryTone(eq!.rating) : "text-ink-muted"
          }`}
        >
          {available ? eq!.rating : "Awaiting intraday data"}
        </div>
      </div>
      <div className="text-right">
        {available ? (
          <div className={`font-mono text-[18px] font-bold ${entryTone(eq!.rating)}`}>
            {eq!.score}
            <span className="text-[11px] font-normal text-ink-muted"> / 100</span>
          </div>
        ) : (
          <div className="font-mono text-[18px] font-bold text-ink-muted">
            —<span className="text-[11px] font-normal"> / 100</span>
          </div>
        )}
        <div className="text-[9.5px] text-ink-muted">Intraday · real-time</div>
      </div>
    </div>
  );
}

function CandidateCard({
  c,
  livePrice,
  onTrade,
  onAnalyze,
}: {
  c: OpportunityScore;
  livePrice: number | null;
  onTrade: () => void;
  onAnalyze: () => void;
}) {
  return (
    <div className="cq-glass flex flex-col rounded-[18px] p-[18px]">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="text-[18px] font-bold tracking-tight text-ink-primary">{c.symbol}</div>
          <div className="mt-px font-mono text-[12.5px] text-ink-secondary">
            {livePrice != null ? (
              <>
                {livePrice.toLocaleString("en-US", { style: "currency", currency: "USD" })}{" "}
                <span className="text-[#5ea8ff]">live</span>
              </>
            ) : (
              <span className="text-ink-muted">—</span>
            )}
          </div>
        </div>
        <div className="text-right">
          <div className="text-[9.5px] font-semibold uppercase tracking-[0.12em] text-ink-muted">
            Setup Strength
          </div>
          <div className={`font-mono text-[20px] font-bold ${bandTone(c.label)}`}>
            {c.score}
            <span className="text-[12px] font-normal text-ink-muted"> / 100</span>
          </div>
          <div className={`text-[11px] font-semibold ${bandTone(c.label)}`}>{c.label}</div>
        </div>
      </div>

      <EntryQualityRow eq={c.entry_quality} />

      <div className="mt-3 space-y-1">
        {c.factors.map((f) => (
          <div key={f.name} className="flex items-center gap-2 text-[11px]">
            <span className="w-20 shrink-0 text-ink-muted">{FACTOR_LABEL[f.name] ?? f.name}</span>
            <div className="h-1.5 flex-1 overflow-hidden rounded bg-surface-2">
              <div
                className="h-full rounded bg-brand-blue"
                style={{ width: `${f.score !== null ? (f.score / f.max_score) * 100 : 0}%` }}
              />
            </div>
            <span className="w-10 shrink-0 text-right tabular-nums text-ink-secondary">
              {f.score}/{f.max_score}
            </span>
          </div>
        ))}
      </div>

      <div className="mt-2 text-[10.5px] text-ink-muted">
        Rule-based · {c.factor_coverage} · data {c.freshness}
      </div>

      <div className="mt-4 flex gap-2">
        <button
          onClick={onAnalyze}
          className="flex flex-1 items-center justify-center gap-1.5 rounded-xl bg-brand-blue px-3.5 py-2.5 text-[13px] font-semibold text-white transition-colors hover:bg-brand-blue/90"
        >
          Analysis
        </button>
        <button
          onClick={onTrade}
          className="rounded-xl border border-border-strong px-3.5 py-2.5 text-[13px] font-semibold text-ink-secondary transition-colors hover:border-brand-blue hover:text-ink-primary"
        >
          Trade
        </button>
      </div>
    </div>
  );
}

export default function TradeCenterPage({
  onTrade,
  onViewAnalysis,
  onNavigate,
}: TradeCenterPageProps) {
  const [candidates, setCandidates] = useState<OpportunityScore[] | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // The backend serves the scan from a warm cache and returns a fast "warming
  // up" placeholder (empty candidates + a warming note) when it's cold, rather
  // than blocking the request on a multi-second cold scan. So we never hang on
  // the spinner; instead, when we get the warming placeholder, we poll (bypassing
  // the 30s share cache) until the background warm fills in real candidates.
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let attempts = 0;
    const MAX_ATTEMPTS = 24; // ~2 min at 5s spacing

    const isWarming = (scan: OpportunityScan) =>
      scan.candidates.length === 0 && !!scan.note && /warming/i.test(scan.note);

    const load = (useShared: boolean) => {
      (useShared ? getOpportunityScanShared(4) : getOpportunityScan(4))
        .then((scan) => {
          if (!alive) return;
          setCandidates(scan.candidates);
          setNote(scan.note);
          setLoading(false);
          if (isWarming(scan) && attempts < MAX_ATTEMPTS) {
            attempts += 1;
            timer = setTimeout(() => load(false), 5000); // non-shared so we see fresh state
          }
        })
        .catch((e) => {
          if (!alive) return;
          setError(e instanceof ApiError ? e.message : "Could not load candidates.");
          setLoading(false);
        });
    };

    setLoading(true);
    setError(null);
    load(true);
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, []);

  // Live candidate prices track the loaded set of candidates through the shared
  // 15s cache. The opportunity scan itself is slow-changing and stays one-shot.
  const candidateSymbols = useMemo(
    () => candidates?.map((c) => c.symbol) ?? [],
    [candidates]
  );
  const priceQuery = useLiveQuotes(candidateSymbols);
  const prices = useMemo(() => {
    const m: Record<string, number | null> = {};
    (priceQuery.data ?? []).forEach(
      (r) => (m[r.symbol.toUpperCase()] = r.status === "ok" ? r.price : null)
    );
    return m;
  }, [priceQuery.data]);

  return (
    <div>
      <WorkflowBar current={2} onNavigate={onNavigate} />

      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <span className="font-mono text-xs font-semibold uppercase tracking-[0.18em] text-[#5ea8ff]">
            Trade Center
          </span>
          <h1 className="mt-2 text-[clamp(26px,3vw,34px)] font-bold tracking-[-0.025em] text-ink-primary">
            Highest-conviction setups
          </h1>
          <p className="mt-1 max-w-[64ch] text-[14.5px] text-ink-secondary">
            The top candidates from a universe scan, ranked by the Rule-Based Opportunity Score
            (Setup Strength). Each card also shows an independent, real-time{" "}
            <span className="text-ink-primary">Entry Quality</span> score — Setup Strength asks
            "is this a strong <em>stock</em>?"; Entry Quality asks "is this a good{" "}
            <em>moment</em> to enter?", so a strong name can still be a poor entry when extended.
            Technical only — not a probability of profit, not an AI/ML prediction, and not a
            buy/sell instruction.
          </p>
        </div>
      </div>

      <div className="mt-5">
        {loading && (
          <div className="flex items-center gap-2 text-sm text-ink-secondary">
            <Loader2 size={14} className="animate-spin" /> Scanning the universe…
          </div>
        )}

        {error && (
          <div className="flex items-start gap-2 rounded-lg border border-status-critical/40 bg-status-critical-soft px-3 py-2 text-xs text-status-critical">
            <AlertTriangle size={13} className="mt-0.5 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {!loading && !error && candidates && candidates.length === 0 && (
          <div className="flex items-start gap-2 rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm text-ink-secondary">
            <Info size={14} className="mt-0.5 shrink-0" />
            <span>{note ?? "No symbols currently meet the rule-based eligibility criteria."}</span>
          </div>
        )}

        {candidates && candidates.length > 0 && (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {candidates.map((c) => (
              <CandidateCard
                key={c.symbol}
                c={c}
                livePrice={prices[c.symbol.toUpperCase()] ?? null}
                onTrade={() => onTrade(c.symbol)}
                onAnalyze={() => onViewAnalysis(c.symbol)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
