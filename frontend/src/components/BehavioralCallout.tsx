import { Brain } from "lucide-react";
import type { BehavioralSignal } from "../types";

/**
 * Omit entirely when no behavior is detected rather than showing an empty
 * state - absence of a signal is itself informative (build spec §10.4).
 */
export default function BehavioralCallout({ signal }: { signal: BehavioralSignal }) {
  return (
    <div className="rounded-lg border border-brand-blue/30 bg-brand-blue/10 px-3 py-2.5">
      <div className="flex items-center gap-2 text-sm font-semibold text-ink-primary">
        <Brain size={15} className="text-brand-blue" />
        {signal.label}
      </div>
      <p className="mt-1 text-xs leading-relaxed text-ink-secondary">{signal.description}</p>
    </div>
  );
}
