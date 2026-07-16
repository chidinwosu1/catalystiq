/**
 * Deliberately different visual language than ProbabilityBar (accent dots on
 * a muted card, not the success/warning/danger colors) - build spec §8.1
 * rule 2: a user must never be able to subconsciously read "high confidence"
 * as "bullish."
 */
export default function ConfidenceMeter({ confidence }: { confidence: number }) {
  const filled = Math.round(confidence / 10);

  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
      <div className="flex gap-1" role="img" aria-label={`Confidence ${confidence} out of 100`}>
        {Array.from({ length: 10 }, (_, i) => (
          <span
            key={i}
            className={`h-2 w-2 rounded-full ${i < filled ? "bg-brand-blue" : "bg-white/10"}`}
          />
        ))}
      </div>
      <div className="flex items-baseline gap-1 whitespace-nowrap">
        <span className="text-sm font-semibold text-ink-primary">{confidence}</span>
        <span className="text-[11px] text-ink-muted">/100 confidence</span>
      </div>
    </div>
  );
}
