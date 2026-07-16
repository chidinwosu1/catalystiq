export type Rating = "Strong Buy" | "Buy" | "Hold" | "Sell" | "Strong Sell";

export interface ProbabilitySplit {
  bullish: number;
  neutral: number;
  bearish: number;
}

export type Contribution = "+" | "-" | "neutral";

export interface Driver {
  icon: string;
  label: string;
  detail: string;
  contribution: Contribution;
}

export interface BehavioralSignal {
  label: string;
  description: string;
}

export interface TrackRecord {
  accuracyPct: number;
  sampleSize: number;
  windowLabel: string;
}

export interface AnalysisReport {
  ticker: string;
  companyName: string;
  timeframeLabel: string;
  rating: Rating;
  probability: ProbabilitySplit;
  confidence: number;
  expectedMove: string;
  invalidation: string;
  drivers: Driver[];
  behavioralSignal?: BehavioralSignal;
  trackRecord?: TrackRecord;
  dataQualityWarning?: string;
}
