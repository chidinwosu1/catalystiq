import type { ReactNode } from "react";
import { Check, Clock, Send, Shield, TrendingUp, TriangleAlert, Zap } from "lucide-react";
import RatingBadge from "./RatingBadge";
import InfoTooltip from "./InfoTooltip";
import DemoBadge from "./DemoBadge";
import { riskRole, roleClasses } from "../lib/theme";
import { usePreferences, STYLE_LABEL, RISK_LABEL, HOLD_BY_STYLE } from "../lib/preferences";
import type { OpportunityDetail } from "../mockTradeCenter";

/**
 * The personalized Investment Strategy view: turns an opportunity into a plan
 * framed for the user's own preferences (from the shared Preferences store),
 * with an info tooltip on every scored section.
 */
function parsePrice(s: string): number | null {
  const n = parseFloat(s.replace(/[$,]/g, ""));
  return Number.isFinite(n) ? n : null;
}
function parsePct(s: string): number | null {
  const m = s.match(/-?\d+(\.\d+)?\s*%/);
  return m ? Math.abs(parseFloat(m[0])) : null;
}

function Section({
  title,
  info,
  children,
  className = "",
  right,
}: {
  title: string;
  info: ReactNode;
  children: ReactNode;
  className?: string;
  right?: ReactNode;
}) {
  return (
    <section className={`cq-glass rounded-2xl p-[18px] ${className}`}>
      <div className="mb-3 flex items-center gap-2">
        <h2 className="text-sm font-semibold text-ink-primary">{title}</h2>
        <InfoTooltip label={title}>{info}</InfoTooltip>
        {right && <span className="ml-auto">{right}</span>}
      </div>
      {children}
    </section>
  );
}

function EvidenceList({ items, warn = false }: { items: string[]; warn?: boolean }) {
  return (
    <ul className="flex flex-col gap-2">
      {items.map((t) => (
        <li key={t} className="flex gap-2.5 text-[13px] text-ink-secondary">
          {warn ? (
            <TriangleAlert size={15} className="mt-0.5 shrink-0 text-status-warning" />
          ) : (
            <Check size={15} className="mt-0.5 shrink-0 text-[#5ea8ff]" />
          )}
          <span>{t}</span>
        </li>
      ))}
    </ul>
  );
}

function PlanTile({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-xl border border-border px-3 py-2.5">
      <div className="text-[10px] uppercase tracking-wide text-ink-muted">{label}</div>
      <div className={`mt-0.5 font-mono text-[14px] font-semibold ${tone ?? "text-ink-primary"}`}>
        {value}
      </div>
    </div>
  );
}

export default function StrategyOverview({
  detail,
  livePrice,
  onTrade,
}: {
  detail: OpportunityDetail;
  livePrice: number | null;
  onTrade: (symbol: string) => void;
}) {
  const { prefs } = usePreferences();
  const PROFILE = {
    style: STYLE_LABEL[prefs.style],
    risk: RISK_LABEL[prefs.risk],
    capital: prefs.amount,
    maxLossPct: prefs.maxLossPct,
    hold: HOLD_BY_STYLE[prefs.style],
  };
  const risk = roleClasses[riskRole(detail.risk)];
  const price = livePrice ?? parsePrice(detail.price);

  // Position sizing framed to the user's capital + max-loss preference.
  let sizingText: string;
  if (price && price > 0) {
    const shares = Math.max(1, Math.round((PROFILE.capital * 0.69) / price));
    const posValue = shares * price;
    const stopPct = parsePct(detail.stop) ?? PROFILE.maxLossPct;
    const riskDollars = posValue * (stopPct / 100);
    const riskPct = (riskDollars / PROFILE.capital) * 100;
    const within = riskPct <= PROFILE.maxLossPct;
    sizingText = `Tailored to you: about ${shares} shares (~$${Math.round(
      posValue
    ).toLocaleString()}, ${Math.round(
      (posValue / PROFILE.capital) * 100
    )}% of capital) puts risk near $${Math.round(riskDollars).toLocaleString()} if the stop triggers — roughly ${riskPct.toFixed(
      1
    )}% of your $${PROFILE.capital.toLocaleString()}, ${
      within ? "inside" : "just over"
    } your ${PROFILE.maxLossPct}% max-loss limit. Scale in half now, half on a confirmed break of resistance.`;
  } else {
    sizingText = `Size the position to your $${PROFILE.capital.toLocaleString()} capital and ${PROFILE.maxLossPct}% max-loss preference — keep risk on any single trade within that limit.`;
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <span className="font-mono text-xs font-semibold uppercase tracking-[0.18em] text-[#5ea8ff]">
            Investment Strategy
          </span>
          <h1 className="mt-2 text-[clamp(24px,3vw,32px)] font-bold tracking-[-0.025em] text-ink-primary">
            Your strategy for <span className="text-[#5ea8ff]">{detail.symbol}</span>
          </h1>
          <p className="mt-1 text-[14px] text-ink-secondary">
            {detail.companyName}
            {price != null && (
              <>
                {" · "}
                <span className="font-mono text-ink-primary">
                  {price.toLocaleString("en-US", { style: "currency", currency: "USD" })}
                </span>{" "}
                live
              </>
            )}
          </p>
          <div className="mt-3.5 flex flex-wrap gap-2">
            {[
              { icon: Send, text: `${PROFILE.style} trade` },
              { icon: Shield, text: `${PROFILE.risk} risk` },
              { icon: TrendingUp, text: `$${PROFILE.capital.toLocaleString()} capital` },
              { icon: Clock, text: `Hold ${PROFILE.hold}` },
            ].map(({ icon: Icon, text }) => (
              <span
                key={text}
                className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface px-3 py-1.5 text-[12.5px] text-ink-secondary"
              >
                <Icon size={13} className="text-[#5ea8ff]" />
                {text}
              </span>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-2.5">
          <RatingBadge rating={detail.rating} />
          <DemoBadge />
        </div>
      </div>

      {/* Why this fits you */}
      <div className="cq-glass flex items-start gap-3.5 rounded-2xl p-5">
        <span className="grid h-[38px] w-[38px] shrink-0 place-items-center rounded-xl border border-brand-blue/40 bg-brand-blue/15 text-[#5ea8ff]">
          <Check size={20} />
        </span>
        <div>
          <h3 className="mb-1 text-sm font-semibold text-ink-primary">Why this fits you</h3>
          <p className="text-[13.5px] leading-relaxed text-ink-secondary">
            A {PROFILE.style.toLowerCase()}-trade setup that matches your{" "}
            <b className="text-ink-primary">{PROFILE.risk.toLowerCase()}</b> risk profile and{" "}
            <b className="text-ink-primary">{PROFILE.hold}</b> horizon. Sized to your{" "}
            <b className="text-ink-primary">${PROFILE.capital.toLocaleString()}</b> and{" "}
            <b className="text-ink-primary">{PROFILE.maxLossPct}% max-loss</b> preference, so your
            downside stays within the limit you set.
          </p>
        </div>
      </div>

      {/* Next step · Confirm trade (kept near the top so the hand-off is always in reach) */}
      <div className="flex flex-col items-center gap-3 rounded-2xl border border-brand-blue/25 bg-gradient-to-r from-brand-blue/10 to-transparent p-4 sm:flex-row sm:justify-between">
        <div>
          <p className="text-[13px] font-semibold uppercase tracking-wide text-[#5ea8ff]">
            Next step · Confirm trade
          </p>
          <p className="mt-0.5 text-[14px] text-ink-secondary">
            Happy with the plan? Take it to the ticket and set your risk controls.
          </p>
        </div>
        <button
          onClick={() => onTrade(detail.symbol)}
          className="inline-flex shrink-0 items-center gap-2 rounded-xl bg-brand-blue px-5 py-2.5 text-[13px] font-semibold text-white transition-transform hover:-translate-y-0.5"
        >
          Confirm Trade · {detail.symbol}
          <Send size={15} />
        </button>
      </div>

      {/* Sections */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Section
          className="md:col-span-2"
          title="Investment overview"
          info={
            <>
              <b className="text-ink-primary">Investment overview.</b> A plain-language snapshot of
              the opportunity — what it is and why it's on your radar right now.
            </>
          }
        >
          <p className="text-[13.5px] leading-relaxed text-ink-secondary">{detail.summary}</p>
        </Section>

        <Section
          className="md:col-span-2"
          title="Strategy summary"
          info={
            <>
              <b className="text-ink-primary">Strategy summary.</b> The suggested plan for your
              profile — how to approach entry, holding, and exits. A framework to adapt, not advice.
            </>
          }
        >
          <p className="text-[13.5px] leading-relaxed text-ink-secondary">{detail.strategy}</p>
        </Section>

        <Section
          className="md:col-span-2"
          title="Scores"
          info={
            <>
              <b className="text-ink-primary">Opportunity &amp; confidence.</b> Opportunity is a
              0-100 read of how strong the setup is right now; confidence is how much independent
              evidence agrees. Neither predicts a specific price.
            </>
          }
        >
          <div className="grid grid-cols-2 gap-3">
            <div>
              <div className="text-[11px] text-ink-muted">Opportunity</div>
              <div className="font-mono text-[26px] font-bold text-[#5ea8ff]">
                {detail.catalystScore}
              </div>
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface-2">
                <span
                  className="block h-full rounded-full bg-brand-blue"
                  style={{ width: `${detail.catalystScore}%` }}
                />
              </div>
            </div>
            <div>
              <div className="text-[11px] text-ink-muted">Confidence</div>
              <div className="font-mono text-[26px] font-bold text-status-good">
                {detail.confidence}
              </div>
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface-2">
                <span
                  className="block h-full rounded-full bg-status-good"
                  style={{ width: `${detail.confidence}%` }}
                />
              </div>
            </div>
          </div>
        </Section>

        <Section
          title="Risk assessment"
          right={<span className={`text-[12px] font-bold ${risk.text}`}>{detail.risk}</span>}
          info={
            <>
              <b className="text-ink-primary">Risk assessment.</b> Our read on the downside — how
              much this could move against you and what would invalidate the setup. Use it to size
              the position and place your stop.
            </>
          }
        >
          <p className="text-[13.5px] leading-relaxed text-ink-secondary">{detail.riskText}</p>
        </Section>

        <Section
          title="Market environment"
          info={
            <>
              <b className="text-ink-primary">Market environment.</b> The broad backdrop — regime,
              sector leadership, and how conditions favor or fight this setup.
            </>
          }
        >
          <p className="text-[13.5px] leading-relaxed text-ink-secondary">{detail.market}</p>
        </Section>

        <Section
          title="Technical analysis"
          info={
            <>
              <b className="text-ink-primary">Technical analysis.</b> What price and volume are doing
              — trend, momentum, and key levels — from live data. It describes behavior, not a
              guaranteed direction.
            </>
          }
        >
          <p className="text-[13.5px] leading-relaxed text-ink-secondary">{detail.technical}</p>
        </Section>

        <Section
          title="Investor psychology"
          info={
            <>
              <b className="text-ink-primary">Investor psychology.</b> How the crowd is likely to
              react to this name's recent triggers, and what would push that reaction. Aggregate
              behavior, never about any individual.
            </>
          }
        >
          <p className="text-[13.5px] leading-relaxed text-ink-secondary">
            Positioning and momentum shape the near-term path: a confirming follow-through extends
            the move, while a failed retest invites faster profit-taking.
          </p>
        </Section>

        <Section
          className="md:col-span-2"
          title="Economic & company catalysts"
          info={
            <>
              <b className="text-ink-primary">Catalysts.</b> The scheduled or developing events most
              likely to move this name.
            </>
          }
        >
          <ul className="flex flex-col gap-2">
            {detail.catalysts.map((c) => (
              <li key={c} className="flex gap-2.5 text-[13px] text-ink-secondary">
                <Zap size={15} className="mt-0.5 shrink-0 text-[#5ea8ff]" />
                <span>{c}</span>
              </li>
            ))}
          </ul>
        </Section>

        <Section
          className="md:col-span-2"
          title="Suggested trade management"
          info={
            <>
              <b className="text-ink-primary">Suggested trade management.</b> A starting plan for
              entry, exit, stop, and size — scaled to your investment amount and max-loss
              preference. A framework to adapt, not advice.
            </>
          }
        >
          <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
            <PlanTile label="Suggested entry" value={detail.entry} tone="text-[#5ea8ff]" />
            <PlanTile label="Profit target" value={detail.target} tone="text-status-good" />
            <PlanTile label="Stop loss" value={detail.stop} tone="text-status-critical" />
            <PlanTile label="Suggested exit" value={detail.exit} />
          </div>
          <div className="mt-3 rounded-xl border border-dashed border-border-strong px-3.5 py-3 text-[13px] text-ink-secondary">
            {sizingText}
          </div>
        </Section>

        <Section
          title="Supporting evidence"
          info={
            <>
              <b className="text-ink-primary">Supporting evidence.</b> The independent observations
              behind the scores — the receipts you can check.
            </>
          }
        >
          <EvidenceList items={detail.evidence} />
        </Section>

        <Section
          title="Key considerations"
          info={
            <>
              <b className="text-ink-primary">Key considerations.</b> The caveats worth weighing
              before you act — what could go wrong and what to watch.
            </>
          }
        >
          <EvidenceList
            warn
            items={[
              `Risk is rated ${detail.risk.toLowerCase()} — size accordingly and honor the stop`,
              "Catalysts can swing the whole peer group, not just this name",
              "A pullback entry near support improves your risk-to-reward",
            ]}
          />
        </Section>
      </div>
    </div>
  );
}
