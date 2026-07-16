import { AlertTriangle } from "lucide-react";

/**
 * Outranks the rating badge in urgency (build spec §10.4) - a rating
 * computed on flagged data shouldn't look as trustworthy as one that isn't.
 */
export default function DataQualityBanner({ message }: { message: string }) {
  return (
    <div className="mb-3 flex items-start gap-2 rounded-lg border border-status-warning/40 bg-status-warning-soft px-3 py-2 text-xs text-status-warning">
      <AlertTriangle size={14} className="mt-0.5 shrink-0" />
      <span>{message}</span>
    </div>
  );
}
