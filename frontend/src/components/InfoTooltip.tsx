import { useEffect, useRef, useState, type ReactNode } from "react";

/**
 * Small "i" affordance next to a section title. Explains what a metric means,
 * why it matters, and what broadly feeds it — never the formula, weights, or
 * model internals. Reusable across every scored section in the app.
 */
export default function InfoTooltip({ label, children }: { label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <span ref={ref} className="relative inline-flex align-middle">
      <button
        type="button"
        aria-label={`About ${label}`}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="grid h-[18px] w-[18px] place-items-center rounded-full border border-border-strong text-[11px] font-bold leading-none text-ink-muted transition-colors hover:border-[#5ea8ff] hover:text-ink-primary"
      >
        i
      </button>
      {open && (
        <span
          role="tooltip"
          className="absolute left-[-8px] top-[26px] z-30 w-[280px] rounded-xl border border-border-strong bg-surface-2 p-3 text-[12.5px] leading-relaxed text-ink-secondary shadow-[0_16px_40px_rgba(0,0,0,0.5)]"
        >
          {children}
        </span>
      )}
    </span>
  );
}
