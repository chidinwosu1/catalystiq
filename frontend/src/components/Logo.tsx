type LogoProps = {
  size?: "sm" | "md" | "lg";
  withWordmark?: boolean;
  className?: string;
};

const MARK_SIZE: Record<NonNullable<LogoProps["size"]>, number> = {
  sm: 28,
  md: 40,
  lg: 88,
};

const WORDMARK_SIZE: Record<NonNullable<LogoProps["size"]>, string> = {
  sm: "text-sm",
  md: "text-lg",
  lg: "text-4xl",
};

export function LogoMark({ size = 40 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size * 0.9}
      viewBox="0 0 200 180"
      role="img"
      aria-label="Catalyst IQ"
    >
      <defs>
        <linearGradient id="ciq-silver" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#f1f3f7" />
          <stop offset="35%" stopColor="#b7bec9" />
          <stop offset="62%" stopColor="#7d8494" />
          <stop offset="100%" stopColor="#d8dde4" />
        </linearGradient>
        <linearGradient id="ciq-blue" x1="0%" y1="0%" x2="0%" y2="100%">
          <stop offset="0%" stopColor="#8ec0f7" />
          <stop offset="55%" stopColor="#3987e5" />
          <stop offset="100%" stopColor="#1c5cab" />
        </linearGradient>
        {/* C: full annulus, wedge-cut open on the right */}
        <mask id="ciq-c-mask">
          <circle cx="92" cy="94" r="80" fill="white" />
          <circle cx="92" cy="94" r="48" fill="black" />
          <polygon points="92,94 182,26 182,162" fill="black" />
        </mask>
        {/* Q: full annulus, no cut */}
        <mask id="ciq-q-mask">
          <circle cx="134" cy="104" r="54" fill="white" />
          <circle cx="134" cy="104" r="32" fill="black" />
        </mask>
      </defs>

      <circle cx="92" cy="94" r="80" fill="url(#ciq-silver)" mask="url(#ciq-c-mask)" />
      <circle cx="134" cy="104" r="54" fill="url(#ciq-silver)" mask="url(#ciq-q-mask)" />
      <polygon points="150,132 179,160 160,160 138,138" fill="url(#ciq-blue)" />
      <rect x="98" y="42" width="17" height="108" fill="url(#ciq-blue)" />
    </svg>
  );
}

export default function Logo({ size = "md", withWordmark = true, className = "" }: LogoProps) {
  return (
    <div className={`flex items-center gap-3 ${className}`}>
      <LogoMark size={MARK_SIZE[size]} />
      {withWordmark && (
        <span className={`font-semibold tracking-[0.14em] ${WORDMARK_SIZE[size]}`}>
          <span className="bg-gradient-to-b from-slate-100 via-slate-300 to-slate-400 bg-clip-text text-transparent">
            CATALYST
          </span>{" "}
          <span className="bg-gradient-to-b from-sky-300 via-brand-blue to-blue-700 bg-clip-text text-transparent">
            IQ
          </span>
        </span>
      )}
    </div>
  );
}
