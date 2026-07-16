import {
  Building2,
  LineChart,
  Newspaper,
  SlidersHorizontal,
  TrendingUp,
  Users,
} from "lucide-react";
import type { ComponentType } from "react";
import type { Driver } from "../types";
import { contributionClass, contributionGlyph } from "../lib/theme";

const ICONS: Record<string, ComponentType<{ size?: number; className?: string }>> = {
  "trend-up": TrendingUp,
  options: SlidersHorizontal,
  building: Building2,
  newspaper: Newspaper,
  users: Users,
};

function DriverIcon({ icon }: { icon: string }) {
  const Icon = ICONS[icon] ?? LineChart;
  return <Icon size={15} className="text-ink-muted" />;
}

/**
 * One line per module contribution - the "one level down" audit entry point
 * (build spec §8.2). A real drill-in (full numbers/percentiles) is future
 * work; this is the plain-language evidence-chip layer.
 */
export default function DriverList({ drivers }: { drivers: Driver[] }) {
  return (
    <ul className="divide-y divide-border">
      {drivers.map((driver) => (
        <li key={driver.label} className="flex items-start gap-2.5 py-2.5 first:pt-0 last:pb-0">
          <DriverIcon icon={driver.icon} />
          <div className="min-w-0 flex-1">
            <p className="text-sm text-ink-primary">
              <span className="font-medium">{driver.label}</span>
              <span className="text-ink-secondary"> — {driver.detail}</span>
            </p>
          </div>
          <span
            className={`shrink-0 text-sm font-semibold ${contributionClass(driver.contribution)}`}
            aria-label={
              driver.contribution === "+"
                ? "positive contribution"
                : driver.contribution === "-"
                  ? "negative contribution"
                  : "neutral contribution"
            }
          >
            {contributionGlyph(driver.contribution)}
          </span>
        </li>
      ))}
    </ul>
  );
}
