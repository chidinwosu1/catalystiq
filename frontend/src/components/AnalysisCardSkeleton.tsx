/** Same layout/heights as AnalysisCard so nothing shifts when data arrives (§10.4). */
export default function AnalysisCardSkeleton() {
  return (
    <div className="flex w-full animate-pulse flex-col rounded-xl border border-border bg-surface p-5">
      <div className="mb-4 flex items-start justify-between gap-3">
        <div className="space-y-2">
          <div className="h-3 w-32 rounded bg-surface-2" />
          <div className="h-5 w-44 rounded bg-surface-2" />
        </div>
        <div className="h-6 w-20 rounded-full bg-surface-2" />
      </div>
      <div className="mb-4 space-y-1.5">
        <div className="h-3 w-full rounded bg-surface-2" />
        <div className="h-2.5 w-full rounded-full bg-surface-2" />
      </div>
      <div className="mb-4 h-10 w-full rounded-lg bg-surface-2" />
      <div className="mb-4 grid grid-cols-2 gap-3">
        <div className="h-14 rounded-lg bg-surface-2" />
        <div className="h-14 rounded-lg bg-surface-2" />
      </div>
      <div className="space-y-2">
        <div className="h-4 w-full rounded bg-surface-2" />
        <div className="h-4 w-5/6 rounded bg-surface-2" />
        <div className="h-4 w-2/3 rounded bg-surface-2" />
      </div>
    </div>
  );
}
