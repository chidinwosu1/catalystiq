import type { AnalysisReport } from "../types";
import ProbabilityBar from "./ProbabilityBar";
import ConfidenceMeter from "./ConfidenceMeter";
import RatingBadge from "./RatingBadge";
import DriverList from "./DriverList";
import BehavioralCallout from "./BehavioralCallout";
import TrackRecordFooter from "./TrackRecordFooter";
import DataQualityBanner from "./DataQualityBanner";

export default function AnalysisCard({ report }: { report: AnalysisReport }) {
  return (
    <article className="flex w-full flex-col rounded-xl border border-border bg-surface p-5 shadow-lg shadow-black/20">
      {report.dataQualityWarning && <DataQualityBanner message={report.dataQualityWarning} />}

      {/* Header row */}
      <div className="mb-4 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-medium text-ink-muted">{report.timeframeLabel}</p>
          <h3 className="truncate text-lg font-medium text-ink-primary">
            {report.companyName} <span className="text-ink-secondary">· {report.ticker}</span>
          </h3>
        </div>
        <RatingBadge rating={report.rating} />
      </div>

      <div className="mb-4">
        <ProbabilityBar probability={report.probability} />
      </div>

      <div className="mb-4">
        <ConfidenceMeter confidence={report.confidence} />
      </div>

      <div className="mb-4 grid grid-cols-2 gap-3">
        <div className="rounded-lg border border-border px-3 py-2.5">
          <p className="text-[11px] uppercase tracking-wide text-ink-muted">Expected move</p>
          <p className="mt-0.5 text-sm font-semibold text-ink-primary">{report.expectedMove}</p>
        </div>
        <div className="rounded-lg border border-border px-3 py-2.5">
          <p className="text-[11px] uppercase tracking-wide text-ink-muted">Invalidation</p>
          <p className="mt-0.5 text-sm font-semibold text-ink-primary">{report.invalidation}</p>
        </div>
      </div>

      <div className="mb-4">
        <DriverList drivers={report.drivers} />
      </div>

      {report.behavioralSignal && (
        <div className="mb-4">
          <BehavioralCallout signal={report.behavioralSignal} />
        </div>
      )}

      {report.trackRecord && (
        <div className="mt-auto border-t border-border pt-3">
          <TrackRecordFooter trackRecord={report.trackRecord} />
        </div>
      )}
    </article>
  );
}
