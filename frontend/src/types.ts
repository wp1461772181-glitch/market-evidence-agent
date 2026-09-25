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
  target_window?: { start: string; end: string } | null;
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

export type OfficialFiling = {
  /** Database identity used only when an accepted filing is selected for an evidence revision. */
  id?: string;
  accession_number: string;
  form: string;
  filed_at: string;
  accepted_at?: string | null;
  primary_document: string;
  source_url: string;
  observed_at: string;
  source?: string;
  review_status?: string;
  human_review_note?: string | null;
  reviewed_at?: string | null;
  review_scope_note?: string;
  content_status?: "fetched" | "unavailable" | "not_fetched";
  content_observed_at?: string | null;
  content_excerpt_sha256?: string | null;
  content_truncated?: boolean;
  content_error?: string | null;
};

export type FilingInventory = {
  symbol: string;
  filings: OfficialFiling[];
};

export type FilingScanResult = FilingInventory & {
  cik: string;
  discovered_count: number;
  created_count: number;
  skipped_count: number;
  observed_at: string;
};

export type FilingContent = OfficialFiling & {
  content_status: "fetched" | "unavailable";
  content_observed_at: string | null;
  content_excerpt_sha256: string | null;
  content_truncated: boolean;
  content_error: string | null;
  content_excerpt: string | null;
  cache_hit: boolean;
};

export type FilingReview = OfficialFiling & {
  review_status: "accepted" | "rejected";
  human_review_note: string;
  reviewed_at: string;
  review_scope_note: string;
};

/**
 * A user-supplied report or article.  It is deliberately kept separate from
 * SEC filings: the score records the user's judgement, not a verified fact or
 * a probability supplied by the model.
 */
export type UploadedEvidence = {
  id: string;
  symbol: string;
  title: string;
  source_url: string;
  published_at: string;
  uploaded_at?: string;
  observed_at?: string;
  credibility_stars: number;
  credibility_reason: string;
  impact_severity?: "low" | "medium" | "high";
  filename?: string;
  content_sha256?: string;
  content_preview?: string;
  status?: "unconfirmed";
};

export type UploadedEvidenceInventory = {
  symbol: string;
  items: UploadedEvidence[];
};

export type EvidenceRevisionRequest = {
  parent_snapshot_id: string;
  source_type: "official_filing" | "uploaded_media";
  source_id: string;
  mode: "manual";
};

/** The API may include richer research fields over time; the front end only depends on the immutable link. */
export type EvidenceRevision = {
  id: string;
  symbol: string;
  parent_snapshot_id: string;
  revised_snapshot_id: string;
  source_type: "official_filing" | "uploaded_media";
  source_id: string;
  mode: "manual" | "automatic";
  status: "pending_review";
  review_status: "pending_review";
  evidence_conclusion: string;
  model_probability_changed: false;
  created_at: string;
  source?: {
    title: string;
    url: string;
    published_at: string;
    observed_at: string;
    official_confirmation: boolean;
    status: string;
    credibility_stars: number | null;
    credibility_reason: string | null;
  };
  evidence?: {
    summary: string;
    quote: string;
    model_impact_direction: string;
    direction_status: "review_required";
  };
  probabilities?: {
    numeric_probability_changed: false;
  };
  limitations?: string[];
};

export type EvidenceRevisionInventory = {
  symbol: string;
  revisions: EvidenceRevision[];
};

export type ForecastRunResult = {
  symbol: string;
  cutoff_date?: string;
  target_window?: { start: string; end: string };
  model_version?: string;
  model_status?: "experimental_offline_model";
  limitations?: string[];
};

export type DashboardResponse = {
  symbol: string;
  snapshots: Snapshot[];
  refresh_reports: RefreshReport[];
  evaluation: Evaluation | null;
  price_history?: PriceHistory | null;
};

/** Durable V2 work is intentionally separate from the legacy snapshot archive. */
export type V2SourceRef = {
  source_type: "official_filing" | "uploaded_media";
  source_id: string;
};

export type V2ForecastJob = {
  id: string;
  symbol: string;
  kind: "new" | "manual_revision" | "automatic_revision";
  root_version_id: string | null;
  parent_version_id: string | null;
  source_refs: V2SourceRef[];
  status: "queued" | "running" | "succeeded" | "succeeded_no_change" | "blocked_data" | "failed";
  current_stage: string;
  attempts: number;
  error: { type: string } | null;
  result_version_id: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
};

export type V2Probabilities = Record<"bearish" | "neutral" | "bullish", number>;

export type V2Version = {
  id: string;
  root_id: string;
  parent_version_id: string | null;
  job_id: string;
  version_no: number;
  symbol: string;
  target_contract: Record<string, unknown>;
  decision_at: string;
  market_cutoff_at: string;
  baseline_probabilities: V2Probabilities | null;
  joint_probabilities: V2Probabilities | null;
  model_status: "research_only" | "baseline_only" | "experimental_joint";
  model_manifest: Record<string, unknown>;
  research_report: Record<string, unknown> | null;
  change_reason: string | null;
  trigger_type: string;
  created_at: string;
};

export type V2VersionDetail = V2Version & {
  price_input_manifest: Record<string, unknown>;
  evidence_version_manifest: Array<Record<string, unknown>>;
  feature_snapshot: Record<string, unknown>;
};

export type V2TimelineEntry = Pick<V2Version,
  "id" | "parent_version_id" | "version_no" | "decision_at" | "market_cutoff_at" |
  "baseline_probabilities" | "joint_probabilities" | "model_status" | "change_reason" | "trigger_type" | "created_at"
>;

export type V2Timeline = {
  root_id: string;
  symbol: string;
  target_contract: Record<string, unknown>;
  versions: V2TimelineEntry[];
};

export type V2ForecastRoot = {
  id: string;
  decision_at: string;
  target_end_date: string;
  model_status: "research_only" | "baseline_only" | "experimental_joint";
  latest_version_id: string;
  latest_version_no: number;
  expired: boolean | null;
};

export type V2ForecastRoots = {
  symbol: string;
  roots: V2ForecastRoot[];
};

export type V2MonitorSymbolResult = {
  symbol?: string;
  status: "succeeded" | "incomplete" | "failed";
  discovered_count?: number;
  created_count?: number;
  queued_count?: number;
  skipped_count?: number;
  complete?: boolean;
  error?: string | null;
};

export type V2MonitorRun = {
  id: string;
  status: "running" | "succeeded" | "partial" | "failed";
  started_at: string;
  completed_at: string | null;
  next_due_at: string | null;
  per_symbol_results: Record<string, V2MonitorSymbolResult>;
  error_summary: Record<string, string> | null;
  retry_reason: string | null;
};

export type V2MonitorStatus = {
  health: "not_recorded" | "healthy" | "delayed" | "degraded";
  schedule_interval_seconds: number;
  stale_after_seconds: number;
  last_run: V2MonitorRun | null;
  last_success: { id: string; completed_at: string; last_success_watermark: Record<string, unknown> } | null;
};

export type V2Evaluation = {
  id: string;
  result_version: number;
  status: "pending" | "succeeded" | "blocked_price" | "failed";
  actual_target_close: number | null;
  actual_label: "bearish" | "neutral" | "bullish" | null;
  label_available_at: string | null;
  brier_score: number | null;
  log_loss: number | null;
  direction_correct: boolean | null;
  price_input_version: string | null;
  created_at: string;
};

export type V2EvaluationVersion = {
  id: string;
  version_no: number;
  decision_at: string;
  trigger_type: string;
  model_status: "research_only" | "baseline_only" | "experimental_joint";
  time_mode: "observed" | "historical_research" | "unknown";
  latest_evaluation: V2Evaluation | null;
  evaluation_history: V2Evaluation[];
};

export type V2EvaluationRoot = {
  root_id: string;
  target_contract_hash: string;
  target_end_date: string | null;
  versions: V2EvaluationVersion[];
};

export type V2EvaluationCohort = {
  time_mode: "observed" | "historical_research" | "unknown";
  status: "pending" | "insufficient_samples" | "available";
  sample: {
    root_denominator: number;
    labelled_root_count: number;
    scored_root_count: number;
    unscored_root_count: number;
    version_count: number;
    labelled_version_count: number;
    scored_version_count: number;
  };
  roots: V2EvaluationRoot[];
};

export type V2EvaluationResponse = {
  symbol: string;
  status: "pending" | "insufficient_samples" | "available";
  minimum_scored_roots: number;
  cohorts: Record<"prospective" | "historical_research" | "unknown", V2EvaluationCohort>;
};

export type V2Workspace = {
  symbol: string;
  status: "empty" | "available";
  current_root: { id: string; target_contract: Record<string, unknown> } | null;
  current_version: Pick<V2Version,
    "id" | "version_no" | "decision_at" | "market_cutoff_at" | "model_status" | "baseline_probabilities" | "joint_probabilities"
  > | null;
  pending_job_count: number;
  joint_model_status: "unavailable" | "research_only" | "experimental_joint";
  monitor_status: "not_recorded";
};
