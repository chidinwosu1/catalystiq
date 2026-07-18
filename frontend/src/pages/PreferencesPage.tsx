import { useState, type ReactNode } from "react";
import { ArrowRight, Check } from "lucide-react";
import WorkflowBar from "../components/trade/WorkflowBar";
import type { PageId } from "../types/nav";

interface PreferencesPageProps {
  onNavigate: (page: PageId) => void;
}

type Style = "day" | "intraday" | "swing" | "long";
type Risk = "conservative" | "moderate" | "aggressive";
type Direction = "long" | "both";

const STYLES: { id: Style; label: string; detail: string }[] = [
  { id: "intraday", label: "Intraday", detail: "In and out within the session" },
  { id: "day", label: "Day trading", detail: "Same-day, no overnight risk" },
  { id: "swing", label: "Swing trading", detail: "Hold a few days to weeks" },
  { id: "long", label: "Long-term", detail: "Position over months" },
];
const HOLD_BY_STYLE: Record<Style, string> = {
  intraday: "Minutes to hours",
  day: "Same session",
  swing: "2-10 days",
  long: "1-6 months",
};
const RISKS: { id: Risk; label: string; detail: string }[] = [
  { id: "conservative", label: "Conservative", detail: "Protect capital first" },
  { id: "moderate", label: "Moderate", detail: "Balanced risk & reward" },
  { id: "aggressive", label: "Aggressive", detail: "Chase higher returns" },
];
const ASSETS = ["Stocks", "ETFs", "Options", "Futures"];

function Card({
  step,
  title,
  hint,
  children,
}: {
  step: number;
  title: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <section className="cq-glass rounded-2xl p-5">
      <div className="mb-3.5 flex items-baseline gap-2.5">
        <span className="font-mono text-xs font-semibold text-[#5ea8ff]">
          {String(step).padStart(2, "0")}
        </span>
        <h2 className="text-[15px] font-semibold text-ink-primary">{title}</h2>
        {hint && <span className="ml-auto text-[12px] text-ink-muted">{hint}</span>}
      </div>
      {children}
    </section>
  );
}

function Segment<T extends string>({
  options,
  value,
  onChange,
}: {
  options: { id: T; label: string; detail: string }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
      {options.map((o) => {
        const on = value === o.id;
        return (
          <button
            key={o.id}
            onClick={() => onChange(o.id)}
            className={`rounded-xl border p-3 text-left transition-colors ${
              on
                ? "border-brand-blue/50 bg-brand-blue/10"
                : "border-border bg-surface hover:border-border-strong"
            }`}
          >
            <div className="flex items-center gap-1.5">
              <span className="text-[13.5px] font-semibold text-ink-primary">{o.label}</span>
              {on && <Check size={13} className="text-[#5ea8ff]" />}
            </div>
            <div className="mt-0.5 text-[11.5px] text-ink-secondary">{o.detail}</div>
          </button>
        );
      })}
    </div>
  );
}

export default function PreferencesPage({ onNavigate }: PreferencesPageProps) {
  const [style, setStyle] = useState<Style>("swing");
  const [risk, setRisk] = useState<Risk>("moderate");
  const [direction, setDirection] = useState<Direction>("long");
  const [amount, setAmount] = useState("10000");
  const [maxLoss, setMaxLoss] = useState("5");
  const [assets, setAssets] = useState<string[]>(["Stocks"]);
  const [constraints, setConstraints] = useState("");
  const [saved, setSaved] = useState(false);

  function toggleAsset(a: string) {
    setAssets((prev) => (prev.includes(a) ? prev.filter((x) => x !== a) : [...prev, a]));
  }

  return (
    <div>
      <WorkflowBar current={0} onNavigate={onNavigate} />

      <div className="mb-5">
        <span className="font-mono text-xs font-semibold uppercase tracking-[0.18em] text-[#5ea8ff]">
          Step 1 · Define preferences
        </span>
        <h1 className="mt-2 text-[clamp(26px,3vw,34px)] font-bold tracking-[-0.025em] text-ink-primary">
          How do you invest?
        </h1>
        <p className="mt-1 max-w-[62ch] text-[14.5px] text-ink-secondary">
          Tell Catalyst IQ how you like to trade. Everything downstream — the opportunities you see,
          the strategies suggested, and how risk is framed — adapts to these preferences.
        </p>
      </div>

      <div className="space-y-4">
        <Card step={1} title="Trading style" hint={`Typical hold: ${HOLD_BY_STYLE[style]}`}>
          <Segment options={STYLES} value={style} onChange={setStyle} />
        </Card>

        <Card step={2} title="Risk tolerance">
          <Segment options={RISKS} value={risk} onChange={setRisk} />
        </Card>

        <div className="grid gap-4 sm:grid-cols-2">
          <Card step={3} title="Investment amount">
            <div className="flex items-center gap-2 rounded-xl border border-border bg-surface px-3.5 py-2.5">
              <span className="text-ink-muted">$</span>
              <input
                type="number"
                min={0}
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                className="w-full bg-transparent font-mono text-[15px] text-ink-primary focus:outline-none"
              />
            </div>
            <p className="mt-2 text-[12px] text-ink-muted">
              Sets position sizing and how many opportunities fit your book.
            </p>
          </Card>

          <Card step={4} title="Max acceptable loss" hint="per position">
            <div className="flex items-center gap-2 rounded-xl border border-border bg-surface px-3.5 py-2.5">
              <input
                type="number"
                min={0}
                max={100}
                value={maxLoss}
                onChange={(e) => setMaxLoss(e.target.value)}
                className="w-full bg-transparent font-mono text-[15px] text-ink-primary focus:outline-none"
              />
              <span className="text-ink-muted">%</span>
            </div>
            <p className="mt-2 text-[12px] text-ink-muted">
              Drives stop-loss placement and the risk score on every setup.
            </p>
          </Card>
        </div>

        <Card step={5} title="Preferred asset classes" hint="select any">
          <div className="flex flex-wrap gap-2">
            {ASSETS.map((a) => {
              const on = assets.includes(a);
              const supported = a === "Stocks" || a === "ETFs";
              return (
                <button
                  key={a}
                  onClick={() => supported && toggleAsset(a)}
                  disabled={!supported}
                  title={supported ? undefined : "Coming soon"}
                  className={`inline-flex items-center gap-1.5 rounded-full border px-3.5 py-1.5 text-[13px] transition-colors ${
                    on
                      ? "border-brand-blue/50 bg-brand-blue/10 text-ink-primary"
                      : "border-border bg-surface text-ink-secondary hover:text-ink-primary"
                  } ${!supported ? "cursor-not-allowed opacity-40" : ""}`}
                >
                  {on && <Check size={13} className="text-[#5ea8ff]" />}
                  {a}
                  {!supported && " (soon)"}
                </button>
              );
            })}
          </div>
        </Card>

        <Card step={6} title="Direction">
          <div className="flex gap-2.5">
            {(
              [
                { id: "long", label: "Long only", detail: "Buy to open" },
                { id: "both", label: "Long & short", detail: "Both directions" },
              ] as const
            ).map((d) => {
              const on = direction === d.id;
              return (
                <button
                  key={d.id}
                  onClick={() => setDirection(d.id)}
                  className={`flex-1 rounded-xl border p-3 text-left transition-colors ${
                    on
                      ? "border-brand-blue/50 bg-brand-blue/10"
                      : "border-border bg-surface hover:border-border-strong"
                  }`}
                >
                  <div className="flex items-center gap-1.5">
                    <span className="text-[13.5px] font-semibold text-ink-primary">{d.label}</span>
                    {on && <Check size={13} className="text-[#5ea8ff]" />}
                  </div>
                  <div className="mt-0.5 text-[11.5px] text-ink-secondary">{d.detail}</div>
                </button>
              );
            })}
          </div>
        </Card>

        <Card step={7} title="Portfolio constraints" hint="optional">
          <input
            type="text"
            value={constraints}
            onChange={(e) => setConstraints(e.target.value)}
            placeholder="e.g. No single position over 10% · avoid leveraged products"
            className="w-full rounded-xl border border-border bg-surface px-3.5 py-2.5 text-[14px] text-ink-primary placeholder:text-ink-muted focus:border-brand-blue/50 focus:outline-none"
          />
        </Card>
      </div>

      <div className="mt-6 flex flex-col items-center gap-3 rounded-2xl border border-brand-blue/25 bg-gradient-to-r from-brand-blue/10 to-transparent p-4 sm:flex-row sm:justify-between">
        <div>
          <p className="text-[13px] font-semibold uppercase tracking-wide text-[#5ea8ff]">
            Next step · Scan the market
          </p>
          <p className="mt-0.5 text-[14px] text-ink-secondary">
            {saved
              ? "Preferences saved. Read today's market before you pick a name."
              : "Save your preferences, then read today's market read."}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2.5">
          <button
            onClick={() => setSaved(true)}
            className="rounded-xl border border-border-strong px-4 py-2.5 text-[13px] font-semibold text-ink-secondary transition-colors hover:text-ink-primary"
          >
            {saved ? "Saved ✓" : "Save preferences"}
          </button>
          <button
            onClick={() => onNavigate("markets")}
            className="inline-flex items-center gap-2 rounded-xl bg-brand-blue px-5 py-2.5 text-[13px] font-semibold text-white transition-transform hover:-translate-y-0.5"
          >
            Scan the Market
            <ArrowRight size={16} />
          </button>
        </div>
      </div>
    </div>
  );
}
