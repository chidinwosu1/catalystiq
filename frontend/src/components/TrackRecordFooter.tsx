import { History } from "lucide-react";
import type { TrackRecord } from "../types";

/**
 * Render nothing when trackRecord is undefined - never ship a
 * plausible-looking accuracy number that isn't backed by the backtest
 * harness (build spec §10.4, §8.3).
 */
export default function TrackRecordFooter({ trackRecord }: { trackRecord: TrackRecord }) {
  return (
    <div className="flex items-center gap-1.5 text-[11px] text-ink-muted">
      <History size={12} />
      <span>
        This rating type correct {trackRecord.accuracyPct}% of the time across{" "}
        {trackRecord.sampleSize} signals, {trackRecord.windowLabel}.
      </span>
    </div>
  );
}
