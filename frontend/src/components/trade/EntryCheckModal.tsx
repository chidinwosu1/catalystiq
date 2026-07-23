import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Check,
  CheckCircle2,
  Clock,
  Info,
  TrendingDown,
  X,
  XCircle,
} from "lucide-react";
import { useLiveEntryCheck } from "../../lib/liveData";
import { LIVE_REFRESH_MS } from "../../lib/liveCache";
import type {
  EntryCheck,
  EntryQualityComponent,
  EntryQualityScore,
  EntryReason,
} from "../../lib/api";

// The Entry Check answers four questions in plain language: enter now or wait,
// what price to wait for, why, and where to exit — with NO technical jargon in
// the primary view (indicators live in a collapsed "technical details" section).
// It refreshes on the shared 15s live cadence; the copy/prices are templated
// server-side, so nothing here invents numbers.

type SystemStatus = EntryCheck["system_status"];

const STATUS_ICON: Record<SystemStatus, typeof CheckCircle2> = {
  favorable: CheckCircle2,
  almost_ready: Clock,
  wait_for_pullback: TrendingDown,
  avoid: XCircle,
  data_unavailable: AlertTriangle,
};

// One primary accent per status (text + soft background). Never color-only —
// the status text always states the meaning too.
const STATUS_ACCENT: Record<SystemStatus, { text: string; soft: string; ring: string }> = {
  favorable: { text: "text-status-good", soft: "bg-status-good-soft", ring: "border-status-good/40" },
  almost_ready: {
    text: "text-status-warning",
    soft: "bg-status-warning-soft",
    ring: "border-status-warning/40",
  },
  wait_for_pullback: {
    text: "text-status-neutral",
    soft: "bg-status-neutral-soft",
    ring: "border-status-neutral/40",
  },
  avoid: {
    text: "text-status-critical",
    soft: "bg-status-critical-soft",
    ring: "border-status-critical/40",
  },
  data_unavailable: { text: "text-ink-muted", soft: "bg-surface-2", ring: "border-border" },
};

function money(v: number | null | undefined): string {
  return v == null ? "—" : `$${v.toFixed(2)}`;
}

function componentInput(
  eq: EntryQualityScore | undefined,
  name: string,
  key: string
): number | null {
  const c = eq?.components.find((x) => x.name === name);
  const v = c?.inputs?.[key];
  return typeof v === "number" ? v : null;
}

/** True for ~900ms after `value` changes — used to briefly highlight a changed
 * price or status without a distracting animation. */
function useFlashOnChange(value: unknown): boolean {
  const [flash, setFlash] = useState(false);
  const prev = useRef(value);
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      prev.current = value;
      return;
    }
    if (prev.current !== value) {
      prev.current = value;
      setFlash(true);
      const t = setTimeout(() => setFlash(false), 900);
      return () => clearTimeout(t);
    }
  }, [value]);
  return flash;
}

type Freshness = "current" | "stale" | "unavailable";

function fmtEtTime(ms: number): string {
  try {
    return new Date(ms).toLocaleTimeString("en-US", {
      hour: "numeric",
      minute: "2-digit",
      second: "2-digit",
      timeZone: "America/New_York",
      timeZoneName: "short",
    });
  } catch {
    return new Date(ms).toLocaleTimeString();
  }
}

function ReasonRow({ r }: { r: EntryReason }) {
  const icon =
    r.state === "good" ? (
      <Check size={15} className="text-status-good" aria-hidden />
    ) : r.state === "bad" ? (
      <X size={15} className="text-status-critical" aria-hidden />
    ) : (
      <span
        className="grid h-[15px] w-[15px] place-items-center rounded-full border border-ink-muted text-ink-muted"
        aria-hidden
      />
    );
  const srLabel = r.state === "good" ? "Yes" : r.state === "bad" ? "No" : "Waiting";
  return (
    <li className="flex items-center gap-2.5 text-[13.5px] text-ink-secondary">
      <span className="shrink-0">{icon}</span>
      <span className="sr-only">{srLabel}: </span>
      <span>{r.label}</span>
    </li>
  );
}

function TechRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between py-1 text-[12.5px]">
      <span className="text-ink-muted">{label}</span>
      <span className="font-mono tabular-nums text-ink-secondary">{value}</span>
    </div>
  );
}

export default function EntryCheckModal({
  symbol,
  seed,
  onClose,
  onReviewTrade,
}: {
  symbol: string;
  /** The scan candidate's entry_quality, shown instantly before the first poll. */
  seed: EntryQualityScore | null;
  onClose: () => void;
  onReviewTrade: () => void;
}) {
  const live = useLiveEntryCheck(symbol, true);
  const eq = live.data ?? seed ?? undefined;
  const ec = eq?.entry_check ?? null;

  const closeRef = useRef<HTMLButtonElement>(null);
  const [showTech, setShowTech] = useState(false);

  // A 1s clock so "updated Xs ago / next update in Ys" and the stale state
  // advance without waiting on the 15s poll.
  const [nowTick, setNowTick] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNowTick(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  // Escape to close; focus the close button on open (focus is then preserved
  // across refreshes because updates re-render in place, never remount).
  useEffect(() => {
    closeRef.current?.focus();
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const ageSec = live.lastUpdated ? Math.max(0, Math.floor((nowTick - live.lastUpdated) / 1000)) : null;
  const nextInSec =
    ageSec == null ? null : Math.max(0, Math.ceil(LIVE_REFRESH_MS / 1000) - (ageSec % Math.ceil(LIVE_REFRESH_MS / 1000)));

  const freshness: Freshness = useMemo(() => {
    if (!ec || ec.system_status === "data_unavailable") return "unavailable";
    if (ageSec != null && ageSec > 45) return "stale";
    return "current";
  }, [ec, ageSec]);

  // Stale data must never present a NEW favorable entry. When stale we neutralize
  // the accent and banner instead of showing a green "Entry Looks Favorable".
  const effectiveStatus: SystemStatus =
    freshness === "unavailable"
      ? "data_unavailable"
      : freshness === "stale" && ec?.system_status === "favorable"
        ? "almost_ready"
        : (ec?.system_status ?? "data_unavailable");

  const accent = STATUS_ACCENT[effectiveStatus];
  const StatusIcon = STATUS_ICON[effectiveStatus];
  const statusText =
    freshness === "unavailable"
      ? "Cannot Evaluate Right Now"
      : freshness === "stale" && ec?.system_status === "favorable"
        ? "Almost Ready — Keep Watching"
        : (ec?.user_status ?? "Cannot Evaluate Right Now");

  const priceFlash = useFlashOnChange(ec?.current_price ?? null);
  const statusFlash = useFlashOnChange(ec?.system_status ?? null);

  const canReview =
    freshness === "current" &&
    (effectiveStatus === "favorable" || effectiveStatus === "almost_ready");

  return (
    <>
      <div
        className="cq-backdrop fixed inset-0 z-[80] bg-[rgba(5,8,13,0.6)] backdrop-blur-[2px]"
        onClick={onClose}
        aria-hidden
      />
      <div className="fixed inset-0 z-[90] flex items-start justify-center overflow-y-auto p-4 sm:items-center">
        <div
          role="dialog"
          aria-modal="true"
          aria-label={`Entry Check for ${symbol}`}
          className="cq-glass w-full max-w-[440px] rounded-[20px] border border-border-strong p-5 shadow-[0_30px_80px_rgba(0,0,0,0.5)]"
          onClick={(e) => e.stopPropagation()}
        >
          {/* Header */}
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-muted">
                Entry Check
              </div>
              <div className="text-[19px] font-bold tracking-tight text-ink-primary">{symbol}</div>
            </div>
            <button
              ref={closeRef}
              onClick={onClose}
              aria-label="Close Entry Check"
              className="grid h-8 w-8 place-items-center rounded-lg border border-border-strong text-ink-muted transition-colors hover:border-brand-blue hover:text-ink-primary"
            >
              <X size={16} />
            </button>
          </div>

          {/* 1. Clear answer */}
          <div
            className={`mt-4 flex items-center gap-3 rounded-2xl border ${accent.ring} ${accent.soft} px-4 py-3.5 transition-colors duration-500 ${
              statusFlash ? "ring-2 ring-white/10" : ""
            }`}
          >
            <StatusIcon size={26} className={`shrink-0 ${accent.text}`} aria-hidden />
            <div className={`text-[18px] font-bold leading-tight ${accent.text}`}>{statusText}</div>
          </div>

          {/* Stale / unavailable banner */}
          {freshness === "stale" && (
            <div className="mt-2 flex items-start gap-2 rounded-lg border border-status-warning/40 bg-status-warning-soft px-3 py-2 text-[12px] text-status-warning">
              <AlertTriangle size={13} className="mt-0.5 shrink-0" />
              <span>
                Data may be outdated
                {live.lastUpdated ? <> · Last successful update: {fmtEtTime(live.lastUpdated)}</> : null}
              </span>
            </div>
          )}

          {ec && freshness !== "unavailable" ? (
            <>
              {/* 2. One-sentence explanation */}
              <p className="mt-3 text-[14px] leading-relaxed text-ink-secondary">{ec.headline}</p>

              {/* 3. What to do next */}
              <div className="mt-3 rounded-xl border border-border bg-surface-2/50 px-3.5 py-3">
                <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-muted">
                  What to do
                </div>
                <p className="mt-1 text-[13.5px] leading-relaxed text-ink-primary">{ec.what_to_do}</p>
                <div
                  className={`mt-1.5 font-mono text-[13px] transition-colors duration-500 ${
                    priceFlash ? "text-brand-blue" : "text-ink-secondary"
                  }`}
                >
                  Current price: {money(ec.current_price)}
                </div>
              </div>

              {/* 4. Simple risk summary */}
              <div className="mt-3 rounded-xl border border-border bg-surface-2/50 px-3.5 py-3">
                <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-muted">
                  If you review this trade
                </div>
                <dl className="mt-1.5 space-y-1 text-[13px]">
                  <RiskRow label="Preferred entry">
                    {money(ec.preferred_entry_low)}–{money(ec.preferred_entry_high)}
                  </RiskRow>
                  <RiskRow label="Exit if below">{money(ec.exit_level)}</RiskRow>
                  <RiskRow label="Possible target">{money(ec.target)}</RiskRow>
                  <RiskRow label="Possible loss">
                    {ec.possible_loss_per_share == null
                      ? "—"
                      : `${money(ec.possible_loss_per_share)} per share`}
                  </RiskRow>
                  <RiskRow label="Possible gain">
                    {ec.possible_gain_per_share == null
                      ? "—"
                      : `${money(ec.possible_gain_per_share)} per share`}
                  </RiskRow>
                  <RiskRow label="Gain compared to loss">
                    {ec.reward_to_risk == null ? "—" : `${ec.reward_to_risk.toFixed(1)} : 1`}
                  </RiskRow>
                </dl>
              </div>

              {/* 5. Why? checklist */}
              {ec.reasons.length > 0 && (
                <div className="mt-3">
                  <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-muted">
                    Why?
                  </div>
                  <ul className="mt-1.5 space-y-1.5">
                    {ec.reasons.slice(0, 4).map((r) => (
                      <ReasonRow key={r.key} r={r} />
                    ))}
                  </ul>
                </div>
              )}

              {/* 6. Technical details (collapsed) */}
              <TechnicalDetails eq={eq} ec={ec} show={showTech} onToggle={() => setShowTech((v) => !v)} />
            </>
          ) : (
            <p className="mt-3 text-[13.5px] leading-relaxed text-ink-secondary">
              {ec?.what_to_do ??
                "Live intraday data isn't available yet, so there's nothing to act on."}
            </p>
          )}

          {/* Actions */}
          <div className="mt-4 flex gap-2">
            <button
              onClick={onReviewTrade}
              disabled={!canReview}
              className={`flex flex-1 items-center justify-center rounded-xl px-3.5 py-2.5 text-[13px] font-semibold transition-colors ${
                canReview
                  ? "bg-brand-blue text-white hover:bg-brand-blue/90"
                  : "cursor-not-allowed border border-border bg-surface-2 text-ink-muted"
              }`}
            >
              Review Trade
            </button>
            <button
              onClick={onClose}
              className="rounded-xl border border-border-strong px-3.5 py-2.5 text-[13px] font-semibold text-ink-secondary transition-colors hover:border-brand-blue hover:text-ink-primary"
            >
              Close
            </button>
          </div>

          {/* Freshness footer — never claims "live"; data may be delayed. */}
          <div className="mt-3 flex items-center justify-between text-[10.5px] text-ink-muted">
            <span>
              {live.lastUpdated == null
                ? "Fetching…"
                : `Updated ${ageSec}s ago · Next update in ${nextInSec}s`}
            </span>
            <span className="inline-flex items-center gap-1">
              <Info size={11} /> Market data may be delayed
            </span>
          </div>
        </div>
      </div>
    </>
  );
}

function RiskRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-ink-muted">{label}</dt>
      <dd className="font-mono tabular-nums text-ink-primary">{children}</dd>
    </div>
  );
}

function TechnicalDetails({
  eq,
  ec,
  show,
  onToggle,
}: {
  eq: EntryQualityScore | undefined;
  ec: EntryCheck;
  show: boolean;
  onToggle: () => void;
}) {
  const vwap = componentInput(eq, "vwap_distance", "vwap");
  const ema9 = componentInput(eq, "ema9_distance", "ema9");
  const rsi = componentInput(eq, "intraday_rsi", "intraday_rsi");
  const atr = componentInput(eq, "morning_range_extension", "intraday_atr");
  const rvol = componentInput(eq, "relative_volume", "relative_volume");
  const minsPullback = componentInput(eq, "time_since_pullback", "minutes_since_pullback");
  const mreExt = componentInput(eq, "morning_range_extension", "extension_atr");

  const COMPONENT_LABEL: Record<string, string> = {
    vwap_distance: "VWAP distance",
    ema9_distance: "9-EMA distance",
    intraday_rsi: "Intraday RSI",
    time_since_pullback: "Time since pullback",
    relative_volume: "Relative volume",
    morning_range_extension: "Morning-range extension",
    risk_reward: "Risk / reward",
  };

  return (
    <div className="mt-3 border-t border-border pt-2">
      <button
        onClick={onToggle}
        aria-expanded={show}
        className="flex w-full items-center justify-between text-[12px] font-semibold text-ink-secondary transition-colors hover:text-ink-primary"
      >
        <span>{show ? "Hide technical details" : "View technical details"}</span>
        <span className="text-ink-muted">{show ? "–" : "+"}</span>
      </button>
      {show && (
        <div className="mt-2 rounded-xl border border-border bg-surface-2/40 px-3.5 py-2">
          <TechRow label="VWAP" value={money(vwap)} />
          <TechRow label="9 EMA" value={money(ema9)} />
          <TechRow label="RSI" value={rsi == null ? "—" : rsi.toFixed(1)} />
          <TechRow label="ATR (intraday)" value={money(atr)} />
          <TechRow label="Relative volume" value={rvol == null ? "—" : `${rvol.toFixed(2)}×`} />
          <TechRow
            label="Time since pullback"
            value={minsPullback == null ? "—" : `${Math.round(minsPullback)} min`}
          />
          <TechRow
            label="Morning-range extension"
            value={mreExt == null ? "—" : `${mreExt.toFixed(2)} ATR`}
          />
          <TechRow label="Higher-low confirmation" value={ec.confirmation ? "Yes" : "Not yet"} />
          <div className="mt-2 border-t border-border pt-2">
            <div className="mb-1 text-[10px] font-semibold uppercase tracking-[0.12em] text-ink-muted">
              Entry Quality components
              {eq?.score != null ? ` · ${eq.score}/100` : ""}
            </div>
            {(eq?.components ?? []).map((c: EntryQualityComponent) => (
              <TechRow
                key={c.name}
                label={COMPONENT_LABEL[c.name] ?? c.name}
                value={c.score == null ? "—" : `${c.score}/${c.max_score}`}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
