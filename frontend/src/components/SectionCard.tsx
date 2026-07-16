import type { ReactNode } from "react";

interface SectionCardProps {
  title: string;
  description?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}

export default function SectionCard({
  title,
  description,
  action,
  children,
  className = "",
}: SectionCardProps) {
  return (
    <section className={`rounded-xl border border-border bg-surface p-5 ${className}`}>
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-ink-primary">{title}</h2>
          {description && <p className="mt-0.5 text-xs text-ink-secondary">{description}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}
