import type { ReactNode } from "react";

type Tone = "neutral" | "good" | "critical";

const TONE_CLASS: Record<Tone, string> = {
  neutral: "text-ink-primary",
  good: "text-status-good",
  critical: "text-status-critical",
};

interface StatTileProps {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: Tone;
}

export default function StatTile({ label, value, sub, tone = "neutral" }: StatTileProps) {
  return (
    <div className="rounded-lg border border-border bg-surface px-4 py-3">
      <p className="text-[11px] uppercase tracking-wide text-ink-muted">{label}</p>
      <p className={`mt-1 text-lg font-semibold ${TONE_CLASS[tone]}`}>{value}</p>
      {sub && <p className="mt-0.5 text-xs text-ink-secondary">{sub}</p>}
    </div>
  );
}
