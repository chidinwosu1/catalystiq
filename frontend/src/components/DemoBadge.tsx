/** Consistent marker for sections showing illustrative, not-computed data. */
export default function DemoBadge() {
  return (
    <span className="inline-flex items-center rounded-full border border-status-warning/40 bg-status-warning-soft px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-status-warning">
      Demo data
    </span>
  );
}
