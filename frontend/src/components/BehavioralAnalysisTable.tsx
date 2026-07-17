import { Brain, TrendingDown, TrendingUp } from "lucide-react";
import SectionCard from "./SectionCard";
import DemoBadge from "./DemoBadge";
import type { BehavioralRow } from "../mockBehavioralData";

interface BehavioralAnalysisTableProps {
  title: string;
  description: string;
  rows: BehavioralRow[];
}

/**
 * Renders the build spec's §3 ABC (Antecedent -> Behavior -> Consequence)
 * shape as a table: trigger, antecedent, predicted investor behavior (with
 * what would push it positively/negatively), and the resulting predicted
 * market behavior. Always demo data - see mockBehavioralData.ts.
 */
export default function BehavioralAnalysisTable({
  title,
  description,
  rows,
}: BehavioralAnalysisTableProps) {
  return (
    <SectionCard title={title} description={description} action={<DemoBadge />}>
      <div className="flex items-center gap-2 text-xs text-ink-muted">
        <Brain size={13} />
        Investor Functional Behavior Analysis - crowd/aggregate behavior, never a claim about
        any individual.
      </div>
      <div className="mt-3 space-y-3">
        {rows.map((row, i) => (
          <div key={i} className="rounded-lg border border-border p-3">
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <div>
                <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-muted">
                  Economic / political trigger
                </p>
                <p className="mt-0.5 text-sm text-ink-primary">{row.trigger}</p>
              </div>
              <div>
                <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-muted">
                  Antecedent
                </p>
                <p className="mt-0.5 text-sm text-ink-secondary">{row.antecedent}</p>
              </div>
            </div>

            <div className="mt-3 rounded-lg border border-brand-blue/30 bg-brand-blue/10 px-3 py-2">
              <p className="text-[11px] font-semibold uppercase tracking-wide text-brand-blue">
                Predicted investor behavior
              </p>
              <p className="mt-0.5 text-sm text-ink-primary">{row.investorBehavior}</p>
              <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
                <div className="flex items-start gap-1.5">
                  <TrendingUp size={13} className="mt-0.5 shrink-0 text-status-good" />
                  <p className="text-xs text-ink-secondary">{row.positiveDriver}</p>
                </div>
                <div className="flex items-start gap-1.5">
                  <TrendingDown size={13} className="mt-0.5 shrink-0 text-status-critical" />
                  <p className="text-xs text-ink-secondary">{row.negativeDriver}</p>
                </div>
              </div>
            </div>

            <div className="mt-2">
              <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-muted">
                Predicted market behavior
              </p>
              <p className="mt-0.5 text-sm text-ink-primary">{row.marketBehavior}</p>
            </div>
          </div>
        ))}
      </div>
    </SectionCard>
  );
}
