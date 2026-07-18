import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  Activity,
  ArrowRight,
  ArrowUpRight,
  BarChart3,
  Briefcase,
  CalendarDays,
  CheckCircle2,
  Cpu,
  Eye,
  Globe,
  Layers,
  LayoutGrid,
  LineChart,
  Newspaper,
  Search,
  Shield,
  ShieldCheck,
  Sparkles,
  TrendingUp,
  Users,
} from "lucide-react";
import SignalNetwork from "../components/home/SignalNetwork";
import type { PageId } from "../types/nav";

interface HomePageProps {
  onNavigate: (page: PageId) => void;
  onViewAnalysis: (symbol: string) => void;
}

/** Reveals every descendant `.cq-reveal` as it scrolls into view. */
function useRevealOnScroll() {
  const rootRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    const els = Array.from(root.querySelectorAll<HTMLElement>(".cq-reveal"));
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce || !("IntersectionObserver" in window)) {
      els.forEach((e) => e.classList.add("cq-in"));
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("cq-in");
            io.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.14 }
    );
    els.forEach((e) => io.observe(e));
    return () => io.disconnect();
  }, []);
  return rootRef;
}

const TICKER: { label: string; value: string; change: string; dir: "up" | "dn" }[] = [
  { label: "S&P 500", value: "5,842.31", change: "+0.4%", dir: "up" },
  { label: "NASDAQ", value: "18,203.44", change: "+0.7%", dir: "up" },
  { label: "VIX", value: "16.82", change: "-6.3%", dir: "dn" },
  { label: "10Y", value: "4.31%", change: "+2.4%", dir: "up" },
  { label: "NVDA", value: "138.42", change: "+1.2%", dir: "up" },
  { label: "AVGO", value: "172.90", change: "+3.5%", dir: "up" },
  { label: "UNH", value: "524.10", change: "+0.9%", dir: "up" },
  { label: "Gold", value: "2,614", change: "+0.3%", dir: "up" },
  { label: "WTI", value: "78.42", change: "-0.9%", dir: "dn" },
  { label: "BTC", value: "67,410", change: "+1.6%", dir: "up" },
];

interface Offer {
  title: string;
  lead: string;
  detail: string;
  icon: typeof TrendingUp;
  page: PageId;
}

const OFFERS: Offer[] = [
  {
    title: "Smarter Market Analysis",
    lead: "Hundreds of signals, not a handful.",
    detail:
      "Evaluate hundreds of market signals across technicals, structure, and behavior instead of relying on a few indicators.",
    icon: TrendingUp,
    page: "analysis",
  },
  {
    title: "Confidence-Based Rankings",
    lead: "Conviction that's earned, not assumed.",
    detail:
      "Opportunities rise to the top only when multiple independent sources of market evidence agree.",
    icon: ShieldCheck,
    page: "analysis",
  },
  {
    title: "Investor Psychology",
    lead: "Read how the crowd will react.",
    detail:
      "Understand how investor behavior moves price as conditions shift — and what would change that reaction.",
    icon: Users,
    page: "analysis",
  },
  {
    title: "Risk Comes First",
    lead: "Downside before upside, every time.",
    detail:
      "Every opportunity is weighed for risk before reward, so you see what you're risking as clearly as what you could gain.",
    icon: Shield,
    page: "analysis",
  },
  {
    title: "One Complete Picture",
    lead: "Everything in a single view.",
    detail:
      "Technicals, economic events, company news, and behavioral finance organized into one coherent, readable view.",
    icon: LayoutGrid,
    page: "markets",
  },
  {
    title: "Intelligence That Explains Itself",
    lead: "See the why, not just the what.",
    detail:
      "The model surfaces each opportunity and shows the evidence behind the score — never a black box.",
    icon: Sparkles,
    page: "analysis",
  },
];

const WORKFLOW: { title: string; detail: string }[] = [
  { title: "Define your goals", detail: "Amount, risk, timeframe, preferences." },
  { title: "Scan the market", detail: "Every signal source, evaluated at once." },
  { title: "Review opportunities", detail: "Compare the strongest candidates." },
  { title: "Build your strategy", detail: "See how it fits your goals." },
  { title: "Confirm your trade", detail: "The decision stays yours." },
  { title: "Monitor performance", detail: "Track positions over time." },
];

const POWERS: { label: string; icon: typeof Activity }[] = [
  { label: "Historical Behavior", icon: Activity },
  { label: "Technical Analysis", icon: BarChart3 },
  { label: "Market Structure", icon: LayoutGrid },
  { label: "Volume & Liquidity", icon: Layers },
  { label: "Economic Events", icon: CalendarDays },
  { label: "Company News", icon: Newspaper },
  { label: "Investor Psychology", icon: Users },
  { label: "Risk Analysis", icon: Shield },
  { label: "Machine Learning", icon: Cpu },
  { label: "Data Validation", icon: CheckCircle2 },
];

function Kicker({ children }: { children: ReactNode }) {
  return (
    <span className="font-mono text-xs font-semibold uppercase tracking-[0.18em] text-[#5ea8ff]">
      {children}
    </span>
  );
}

function FlipTile({ offer, onLearnMore }: { offer: Offer; onLearnMore: (p: PageId) => void }) {
  const [flipped, setFlipped] = useState(false);
  const Icon = offer.icon;
  const faceBase = "cq-face cq-glass flex flex-col rounded-2xl px-[17px] py-4";
  return (
    <div
      className={`cq-flip cq-reveal min-h-[158px] cursor-pointer ${flipped ? "is-flipped" : ""}`}
      onClick={() => setFlipped((v) => !v)}
    >
      <div className="cq-flip-inner">
        {/* Front */}
        <div className={faceBase}>
          <div className="mb-2.5 grid h-9 w-9 place-items-center rounded-[10px] border border-brand-blue/40 bg-brand-blue/15 text-[#5ea8ff]">
            <Icon size={19} />
          </div>
          <h3 className="mb-1 text-[15.5px] font-semibold tracking-tight text-ink-primary">
            {offer.title}
          </h3>
          <p className="text-[13px] font-medium text-ink-primary">{offer.lead}</p>
          <span className="mt-auto pt-2.5 text-[11.5px] text-ink-muted">Flip for more →</span>
        </div>
        {/* Back */}
        <div className={`${faceBase} cq-face-back`}>
          <h3 className="mb-1.5 text-[13.5px] font-semibold text-[#5ea8ff]">{offer.title}</h3>
          <p className="text-[12.5px] leading-normal text-ink-secondary">{offer.detail}</p>
          <button
            onClick={(e) => {
              e.stopPropagation();
              onLearnMore(offer.page);
            }}
            className="group mt-auto inline-flex items-center gap-1.5 pt-2.5 text-[12.5px] font-semibold text-ink-primary"
          >
            Learn more
            <ArrowRight size={14} className="transition-transform group-hover:translate-x-1" />
          </button>
        </div>
      </div>
    </div>
  );
}

const secTitle =
  "mt-3 text-[clamp(24px,3vw,34px)] font-bold leading-[1.1] tracking-[-0.025em] text-balance text-ink-primary";

export default function HomePage({ onNavigate }: HomePageProps) {
  const rootRef = useRevealOnScroll();

  const quickActions: { title: string; detail: string; icon: typeof LineChart; page: PageId }[] = [
    { title: "Start New Analysis", detail: "Research any ticker in depth.", icon: LineChart, page: "analysis" },
    { title: "Today's Opportunities", detail: "See the highest-conviction setups.", icon: Search, page: "analysis" },
    { title: "Open Watchlist", detail: "Track the names you're following.", icon: Eye, page: "markets" },
    { title: "Review Portfolio", detail: "Check balances and positions.", icon: Briefcase, page: "portfolio" },
    { title: "Market Overview", detail: "Read today's macro picture.", icon: Globe, page: "markets" },
  ];

  return (
    <div ref={rootRef} className="relative -mx-6 -my-8">
      {/* Ambient color field for the glass panels to refract over */}
      <div className="cq-ambient" aria-hidden="true">
        <span className="h-[620px] w-[620px] bg-[rgba(57,135,229,0.30)]" style={{ top: -140, left: -120 }} />
        <span className="h-[540px] w-[540px] bg-[rgba(94,140,255,0.20)]" style={{ top: "34%", right: -160 }} />
        <span className="h-[560px] w-[560px] bg-[rgba(40,90,180,0.18)]" style={{ bottom: -160, left: "30%" }} />
      </div>

      <div className="relative z-[1]">
      {/* ===================== TICKER (top) ===================== */}
      <div className="overflow-hidden border-b border-border bg-page">
        <div className="flex w-max cq-marquee gap-9 py-2 font-mono text-[12.5px] text-ink-secondary">
          {[...TICKER, ...TICKER].map((t, i) => (
            <span key={i} className="inline-flex items-center gap-1.5 whitespace-nowrap">
              <b className="font-semibold text-ink-primary">{t.label}</b> {t.value}{" "}
              <span className={t.dir === "up" ? "text-status-good" : "text-status-critical"}>
                {t.change}
              </span>
            </span>
          ))}
        </div>
      </div>

      {/* ===================== HERO (compact) ===================== */}
      <header className="relative overflow-hidden">
        <SignalNetwork />
        <div
          className="pointer-events-none absolute left-1/2 top-[-30%] z-[1] h-[520px] w-[820px] -translate-x-1/2 blur-[10px]"
          style={{ background: "radial-gradient(closest-side, rgba(57,135,229,0.2), transparent 70%)" }}
        />
        <div
          className="pointer-events-none absolute inset-x-0 bottom-0 z-[1] h-36"
          style={{ background: "linear-gradient(to bottom, transparent, var(--color-page))" }}
        />
        <div
          className="relative z-[2] mx-auto max-w-3xl px-6 pb-10 pt-10 text-center"
          style={{ animation: "cq-fade-up 0.8s cubic-bezier(0.2,0.7,0.2,1) both" }}
        >
          <span className="cq-glass inline-flex items-center gap-2.5 rounded-full px-3.5 py-1.5 text-[12.5px] font-medium text-ink-secondary">
            <span className="cq-pulse h-1.5 w-1.5 rounded-full bg-status-good shadow-[0_0_0_4px_rgba(34,197,94,0.18)]" />
            Market intelligence · updated continuously
          </span>
          <h1 className="mx-auto mt-3.5 max-w-[17ch] text-[clamp(30px,4.8vw,52px)] font-extrabold leading-[1.05] tracking-[-0.035em] text-balance text-ink-primary">
            Stop guessing.{" "}
            <span className="bg-gradient-to-r from-[#5ea8ff] to-brand-blue bg-clip-text text-transparent">
              Start investing with evidence.
            </span>
          </h1>
          <p className="mx-auto mt-3.5 max-w-[60ch] text-[clamp(14.5px,1.6vw,17px)] text-ink-secondary">
            Technical signals, macroeconomic data, investor psychology, and machine learning —
            combined into one investment-intelligence platform, so every opportunity is backed by
            evidence, not a single opinion.
          </p>
          <div className="mt-7 flex justify-center">
            <button
              onClick={() => onNavigate("analysis")}
              className="inline-flex items-center gap-2.5 rounded-xl bg-brand-blue px-6 py-3 text-[15px] font-semibold text-white shadow-[0_10px_30px_rgba(57,135,229,0.32)] transition-transform hover:-translate-y-0.5"
            >
              <TrendingUp size={17} />
              Start Your Analysis
            </button>
          </div>
        </div>
      </header>

      {/* ===================== OFFERINGS — flip tiles ===================== */}
      <section className="relative z-[2] mx-auto max-w-[1180px] px-6 pb-10 pt-2">
        <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
          <div>
            <Kicker>What Catalyst IQ offers</Kicker>
            <h2 className={secTitle}>The whole picture, at a glance — flip any tile for more.</h2>
          </div>
          <span className="inline-flex items-center gap-1.5 text-[12.5px] text-ink-muted">
            <Sparkles size={14} /> Hover or tap to flip
          </span>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {OFFERS.map((offer) => (
            <FlipTile key={offer.title} offer={offer} onLearnMore={onNavigate} />
          ))}
        </div>
      </section>

      {/* ===================== HOW IT WORKS (compact) ===================== */}
      <section
        className="border-y border-border px-6 py-16"
        style={{ background: "linear-gradient(180deg, transparent, var(--color-page-2, #0c1017), transparent)" }}
      >
        <div className="mx-auto max-w-[1180px]">
          <div className="cq-reveal mx-auto max-w-xl text-center">
            <Kicker>How it works</Kicker>
            <h2 className={secTitle}>From market noise to a confident decision.</h2>
          </div>
          <div className="mt-9 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            {WORKFLOW.map((step, i) => (
              <div
                key={step.title}
                className="cq-reveal cq-glass rounded-2xl p-4 transition-all hover:-translate-y-1 hover:border-brand-blue/45"
              >
                <div className="mb-3 grid h-8 w-8 place-items-center rounded-[9px] border border-brand-blue/30 bg-brand-blue/10 font-mono text-[13px] font-bold text-[#5ea8ff]">
                  {i + 1}
                </div>
                <h4 className="text-[14.5px] font-semibold text-ink-primary">{step.title}</h4>
                <p className="mt-0.5 text-[12.5px] leading-snug text-ink-secondary">{step.detail}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ===================== WHAT POWERS (compact) ===================== */}
      <section className="px-6 py-16">
        <div className="mx-auto max-w-[1180px]">
          <div className="cq-reveal max-w-2xl">
            <Kicker>What powers Catalyst IQ</Kicker>
            <h2 className={secTitle}>Every opportunity stands on a broad base of evidence.</h2>
          </div>
          <div className="mt-8 grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-5">
            {POWERS.map(({ label, icon: Icon }) => (
              <div
                key={label}
                className="cq-reveal cq-glass flex items-center gap-2.5 rounded-xl px-3.5 py-3 transition-all hover:-translate-y-0.5 hover:border-border-strong"
              >
                <span className="shrink-0 text-[#5ea8ff]">
                  <Icon size={20} />
                </span>
                <span className="text-[13px] font-medium text-ink-primary">{label}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ===================== QUICK ACTIONS ===================== */}
      <section className="px-6 pb-16">
        <div className="mx-auto max-w-[1180px]">
          <div className="cq-reveal max-w-2xl">
            <Kicker>Quick actions</Kicker>
            <h2 className={secTitle}>Jump straight in.</h2>
          </div>
          <div className="mt-8 grid grid-cols-2 gap-3.5 sm:grid-cols-3 lg:grid-cols-5">
            {quickActions.map(({ title, detail, icon: Icon, page }) => (
              <button
                key={title}
                onClick={() => onNavigate(page)}
                className="cq-reveal cq-glass group relative overflow-hidden rounded-2xl p-5 text-left transition-all hover:-translate-y-1 hover:border-brand-blue/50"
              >
                <ArrowUpRight
                  size={18}
                  className="absolute right-4 top-4 text-ink-muted transition-all group-hover:translate-x-0.5 group-hover:-translate-y-0.5 group-hover:text-[#5ea8ff]"
                />
                <div className="mb-9 grid h-10 w-10 place-items-center rounded-[11px] border border-brand-blue/35 bg-brand-blue/15 text-[#5ea8ff]">
                  <Icon size={20} />
                </div>
                <h4 className="text-[15px] font-semibold text-ink-primary">{title}</h4>
                <p className="mt-1 text-[12.5px] text-ink-secondary">{detail}</p>
              </button>
            ))}
          </div>
        </div>
      </section>

      {/* ===================== CLOSING CTA ===================== */}
      <section className="px-6 pb-12">
        <div
          className="relative mx-auto max-w-[1180px] overflow-hidden rounded-3xl border border-border-strong px-8 py-14 text-center"
          style={{
            background:
              "radial-gradient(700px 300px at 50% -30%, rgba(57,135,229,0.26), transparent 70%), linear-gradient(180deg, var(--color-surface), var(--color-page))",
          }}
        >
          <h2 className="mx-auto max-w-[18ch] text-[clamp(26px,3.6vw,40px)] font-bold tracking-[-0.03em] text-balance text-ink-primary">
            Understand the market with greater clarity and confidence.
          </h2>
          <p className="mx-auto mt-3.5 max-w-[52ch] text-[16px] text-ink-secondary">
            Start from today's read, review the strongest opportunities, and build a strategy that
            fits how you invest.
          </p>
          <div className="mt-6 flex justify-center">
            <button
              onClick={() => onNavigate("markets")}
              className="inline-flex items-center gap-2.5 rounded-xl bg-brand-blue px-6 py-3.5 text-[15px] font-semibold text-white shadow-[0_10px_30px_rgba(57,135,229,0.32)] transition-transform hover:-translate-y-0.5"
            >
              Begin Scanning the Market
              <ArrowRight size={17} />
            </button>
          </div>
        </div>
      </section>
      </div>
    </div>
  );
}
