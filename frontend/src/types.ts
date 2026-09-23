export type Snapshot = {
  id: string;
  symbol: string;
  feature_trading_date: string;
  feature_as_of_time: string;
  model_version: string;
  model_sha256: string;
  model_manifest_sha256: string;
  feature_export_sha256: string;
  feature_version: string;
  feature_source: string;
  feature_snapshot_mode: string;
  feature_values: Record<string, number>;
  bearish_probability: number;
  neutral_probability: number;
  bullish_probability: number;
  created_at: string;
  version: number;
  parent_snapshot_id: string | null;
  root_snapshot_id: string;
  revision_reason: string | null;
};

export type Claim = {
  claim: string;
  source_id: string;
  evidence_quote: string;
  review_status?: string;
  evidence_note?: string;
};

export type RefreshReport = {
  revision_mode: "rolling_refresh";
  original_snapshot: Pick<Snapshot, "id" | "symbol" | "feature_trading_date" | "feature_as_of_time" | "model_version" | "bearish_probability" | "neutral_probability" | "bullish_probability">;
  revised_snapshot: Pick<Snapshot, "id" | "symbol" | "feature_trading_date" | "feature_as_of_time" | "model_version" | "bearish_probability" | "neutral_probability" | "bullish_probability">;
  probability_delta: Record<"bearish" | "neutral" | "bullish", number>;
  target_windows: { original: { start: string; end: string }; revised: { start: string; end: string } };
  trigger: {
    reason: string;
    document_id: string;
    event_type: string;
    event_date: string;
    summary: string;
    evidence_quote: string;
    source_url: string;
    impact_direction_status: string;
  };
  research_run: {
    id: string;
    status: string;
    current_stage: string;
    error: string | null;
    report: {
      supporting_evidence?: Claim[];
      counter_evidence?: Claim[];
      information_gaps?: string[];
      conclusion?: string;
    } | null;
  };
  limitations: string[];
};

export type Evaluation = {
  artifact_version?: string;
  model_name?: string;
  scope?: string;
  data_as_of_time?: string;
  feature_version?: string;
  snapshot_mode?: string;
  fold_count?: number;
  test_rows?: number;
  models?: Record<string, { accuracy?: number; balanced_accuracy?: number; macro_f1?: number; brier_multiclass?: number; log_loss?: number }>;
  limitations?: string[];
};

export type PriceCandle = {
  trading_date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  benchmark_close?: number | null;
};

export type PriceHistory = {
  source: string;
  latest_trading_date: string | null;
  candles: PriceCandle[];
};

export type DashboardResponse = {
  symbol: string;
  snapshots: Snapshot[];
  refresh_reports: RefreshReport[];
  evaluation: Evaluation | null;
  price_history?: PriceHistory | null;
};
