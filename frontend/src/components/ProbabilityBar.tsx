import type { ProbabilitySplit } from "../types";

const SEGMENTS = [
  { key: "bullish", label: "Bullish", color: "bg-status-good" },
  { key: "neutral", label: "Neutral", color: "bg-status-neutral" },
  { key: "bearish", label: "Bearish", color: "bg-status-critical" },
] as const;

export default function ProbabilityBar({ probability }: { probability: ProbabilitySplit }) {
  return (
    <div>
      <div className="mb-1.5 flex justify-between text-xs">
        {SEGMENTS.map((s) => (
          <span key={s.key} className="text-ink-secondary">
            {s.label} <span className="font-semibold text-ink-primary">{probability[s.key]}%</span>
          </span>
        ))}
      </div>
      <div
        className="flex h-2.5 w-full overflow-hidden rounded-full bg-surface-2"
        role="img"
        aria-label={`Bullish ${probability.bullish}%, neutral ${probability.neutral}%, bearish ${probability.bearish}%`}
      >
        {SEGMENTS.map((s, i) => (
          <div
            key={s.key}
            className={`h-full ${s.color} ${i > 0 ? "ml-0.5" : ""}`}
            style={{ width: `${probability[s.key]}%` }}
          />
        ))}
      </div>
    </div>
  );
}
