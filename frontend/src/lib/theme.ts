import type { Contribution, Rating } from "../types";

export type ColorRole = "success" | "warning" | "danger";

/**
 * The rating badge is a direct, non-negotiable function of the probability
 * split + confidence via the documented thresholds (build spec §2.8) —
 * never a separate judgment call. This maps the resulting rating to a
 * visual role; it does not decide the rating itself.
 */
export function ratingRole(rating: Rating): ColorRole {
  switch (rating) {
    case "Strong Buy":
    case "Buy":
      return "success";
    case "Hold":
      return "warning";
    case "Sell":
    case "Strong Sell":
      return "danger";
  }
}

export const roleClasses: Record<ColorRole, { bg: string; text: string; border: string }> = {
  success: {
    bg: "bg-status-good-soft",
    text: "text-status-good",
    border: "border-status-good/40",
  },
  warning: {
    bg: "bg-status-warning-soft",
    text: "text-status-warning",
    border: "border-status-warning/40",
  },
  danger: {
    bg: "bg-status-critical-soft",
    text: "text-status-critical",
    border: "border-status-critical/40",
  },
};

import type { ImpactLevel, MarketRegime, RiskLevel } from "../mockDashboardData";

/** Market regime → visual role. Bullish reads success, bearish danger. */
export function regimeRole(regime: MarketRegime): ColorRole {
  switch (regime) {
    case "Bullish":
      return "success";
    case "Neutral":
      return "warning";
    case "Bearish":
      return "danger";
  }
}

/** Risk level → visual role. Higher risk reads more alarming. */
export function riskRole(risk: RiskLevel): ColorRole {
  switch (risk) {
    case "Low":
      return "success";
    case "Moderate":
      return "warning";
    case "Elevated":
    case "High":
      return "danger";
  }
}

/** Expected-impact / severity → visual role. */
export function impactRole(impact: ImpactLevel): ColorRole {
  switch (impact) {
    case "High":
      return "danger";
    case "Medium":
      return "warning";
    case "Low":
      return "success";
  }
}

export function contributionGlyph(contribution: Contribution): string {
  if (contribution === "+") return "+";
  if (contribution === "-") return "−";
  return "•";
}

export function contributionClass(contribution: Contribution): string {
  if (contribution === "+") return "text-status-good";
  if (contribution === "-") return "text-status-critical";
  return "text-ink-muted";
}
