import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

/**
 * Global investing preferences. Set on the Define Preferences page and read
 * everywhere a value is referenced (Investment Strategy sizing, the ticket,
 * etc.) so numbers stay in sync across pages rather than going stale.
 */
export type TradingStyle = "intraday" | "day" | "swing" | "long";
export type RiskTolerance = "conservative" | "moderate" | "aggressive";
export type Direction = "long" | "both";

export interface Preferences {
  style: TradingStyle;
  risk: RiskTolerance;
  amount: number; // investment capital, USD
  maxLossPct: number; // max acceptable loss per position, %
  direction: Direction;
  assets: string[];
  constraints: string;
}

const DEFAULT_PREFERENCES: Preferences = {
  style: "swing",
  risk: "moderate",
  amount: 10000,
  maxLossPct: 5,
  direction: "long",
  assets: ["Stocks"],
  constraints: "",
};

export const STYLE_LABEL: Record<TradingStyle, string> = {
  intraday: "Intraday",
  day: "Day",
  swing: "Swing",
  long: "Long-term",
};
export const HOLD_BY_STYLE: Record<TradingStyle, string> = {
  intraday: "Minutes to hours",
  day: "Same session",
  swing: "2-10 days",
  long: "1-6 months",
};
export const RISK_LABEL: Record<RiskTolerance, string> = {
  conservative: "Conservative",
  moderate: "Moderate",
  aggressive: "Aggressive",
};

interface PreferencesContextValue {
  prefs: Preferences;
  update: (patch: Partial<Preferences>) => void;
}

const PreferencesContext = createContext<PreferencesContextValue | null>(null);

export function PreferencesProvider({ children }: { children: ReactNode }) {
  const [prefs, setPrefs] = useState<Preferences>(DEFAULT_PREFERENCES);
  const value = useMemo<PreferencesContextValue>(
    () => ({ prefs, update: (patch) => setPrefs((p) => ({ ...p, ...patch })) }),
    [prefs]
  );
  return <PreferencesContext.Provider value={value}>{children}</PreferencesContext.Provider>;
}

export function usePreferences(): PreferencesContextValue {
  const ctx = useContext(PreferencesContext);
  if (!ctx) throw new Error("usePreferences must be used within a PreferencesProvider");
  return ctx;
}
