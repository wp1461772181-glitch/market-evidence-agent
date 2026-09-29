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

export type MaterialSourceType = "official_filing" | "uploaded_media";
export type MaterialAnalysisStatus = "not_started" | "queued" | "running" | "succeeded" | "failed" | "blocked_data";
export type MaterialItem = {
  symbol: string;
  source_type: MaterialSourceType;
  source_id: string;
  title: string;
  published_at: string | null;
  observed_at: string | null;
  source_url: string;
  latest_analysis_id: string | null;
  latest_analysis_version_no?: number | null;
  latest_analysis_created_at?: string | null;
  latest_analysis_evidence_version_id?: string | null;
  analysis_status: MaterialAnalysisStatus;
  latest_job: MaterialAnalysisJob | null;
  review_status: string;
  coverage: string | null;
  can_view_original: boolean;
  can_download_original: boolean;
};
export type MaterialLibraryResponse = { items: MaterialItem[]; total: number; limit: number; offset: number };
export type MaterialAnalysisJob = {
  job_id: string;
  status: MaterialAnalysisStatus;
  analysis_id: string | null;
  cache_hit: boolean;
  safe_error_code?: string | null;
  current_stage?: string;
  attempts?: number;
  created_at?: string;
  completed_at?: string | null;
};
export type MaterialAnalysisVersion = {
  analysis_id: string;
  source_type: MaterialSourceType;
  source_id: string;
  evidence_version_id: string;
  version_no: number;
  previous_version_id: string | null;
  actual_model: string;
  payload: {
    summary: string;
    facts: Array<{ id: string; statement: string; citations: Array<{ quote: string; start_char: number; end_char: number }> }>;
    supporting: Array<{ id: string; statement: string; rationale: string; fact_ids: string[]; citations: Array<{ quote: string; start_char: number; end_char: number }> }>;
    counter: Array<{ id: string; statement: string; rationale: string; fact_ids: string[]; citations: Array<{ quote: string; start_char: number; end_char: number }> }>;
    uncertainties: Array<{ id: string; statement: string; reason: string; citations: Array<{ quote: string; start_char: number; end_char: number }> }>;
    key_numbers: Array<{ name: string; value_text: string; period: string | null; citations: Array<{ quote: string; start_char: number; end_char: number }> }>;
  };
  source_manifest: Record<string, unknown>;
  created_at: string;
  frozen_text?: string | null;
  source_snapshot?: Record<string, unknown>;
};
export type LocalizationResult = {
  content_kind: "material_analysis" | "forecast_brief";
  content_id: string;
  locale: "en-US";
  source_sha256: string;
  prompt_version: string;
  cache_hit: boolean;
  fields: Record<string, string>;
};
export type MaterialAnalysisHistory = { source_type: MaterialSourceType; source_id: string; items: MaterialAnalysisVersion[] };
export type MaterialOriginal = {
  symbol: string;
  source_type: MaterialSourceType;
  source_id: string;
  title: string;
  source_url: string;
  content_text: string | null;
  content_status: string;
  content_error: string | null;
  coverage: string | null;
  truncated: boolean;
  can_download: boolean;
  can_fetch: boolean;
  document_name: string | null;
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
  time_mode: "observed" | "historical_research";
  requested_decision_at: string | null;
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
export type V2ModelStatus = "research_only" | "baseline_only" | "experimental_joint" | "experimental_jev";
export type ResearchEvidencePointer = { analysis_id: string; section: "facts" | "supporting" | "counter" | "uncertainties"; item_id: string };
export type ResearchMaterialKeyNumber = {
  name: string;
  value_text: string;
  period: string | null;
  citations: Array<{ quote: string; start_char: number; end_char: number }>;
};
export type ResearchMaterialReference = {
  analysis_id: string;
  evidence_version_id: string;
  source_type: "official_filing" | "uploaded_media";
  source_id: string;
  symbol: string;
  title: string | null;
  source_url: string | null;
  published_at: string;
  observed_at: string;
  content_sha256: string;
  analysis_text_sha256: string;
  review_status: string;
  user_rating_stars: number | null;
  user_rating_label: string | null;
  truncated: boolean;
  coverage_incomplete: boolean;
  coverage: string | null;
  selection_reason: string;
  explicitly_selected: boolean;
  analysis_summary?: string | null;
  key_numbers?: ResearchMaterialKeyNumber[];
};
export type ResearchEvidenceText = { statement: string; citations: ResearchEvidencePointer[] };
export type ResearchBrief = {
  schema_version: "research-brief-v1";
  symbol: string;
  decision_at: string;
  time_mode: "observed" | "historical_research";
  target_contract: Record<string, unknown>;
  market_summary: Record<string, unknown>;
  material_refs: ResearchMaterialReference[];
  new_facts: ResearchEvidenceText[];
  supporting: ResearchEvidenceText[];
  counter: ResearchEvidenceText[];
  background: Array<ResearchEvidenceText & { continuing_reason: string }>;
  conflicts: Array<{ description: string; citations: ResearchEvidencePointer[] }>;
  unknowns: Array<{ question: string; reason: string; citations: ResearchEvidencePointer[] }>;
  changes: Array<{ change_type: string; description: string; citations: ResearchEvidencePointer[] }>;
  omitted: Array<{ source_type: string; source_id: string; analysis_id: string | null; reason: string }>;
  input_quality: { status: "ready" | "limited" | "insufficient"; reasons: string[] };
};

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
  model_status: V2ModelStatus;
  decision_probabilities?: V2Probabilities | null;
  research_brief?: ResearchBrief | null;
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
  "baseline_probabilities" | "joint_probabilities" | "decision_probabilities" | "model_status" | "change_reason" | "trigger_type" | "created_at"
> & {
  local_calibration_status?: "active" | "not_applied" | null;
  raw_decision_probabilities?: V2Probabilities | null;
};

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
  model_status: V2ModelStatus;
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
  model_status: V2ModelStatus;
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

export type V2ModelEvaluationVersion = V2EvaluationVersion & {
  provider: string;
  actual_model: string;
  question_version: string | null;
};
export type V2ModelEvaluationRoot = Omit<V2EvaluationRoot, "versions"> & { versions: V2ModelEvaluationVersion[] };
export type V2ModelEvaluationCohort = {
  time_mode: "observed" | "historical_research" | "unknown";
  model_status: V2ModelStatus;
  provider: string;
  actual_model: string;
  question_version: string | null;
  log_loss_zero_probability_floor: number | null;
  status: "pending" | "insufficient_samples" | "available";
  sample: Pick<V2EvaluationCohort["sample"], "root_denominator" | "labelled_root_count" | "scored_root_count" | "unscored_root_count">;
  roots: V2ModelEvaluationRoot[];
};

export type V2EvaluationResponse = {
  symbol: string;
  status: "pending" | "insufficient_samples" | "available";
  minimum_scored_roots: number;
  cohorts: Record<"prospective" | "historical_research" | "unknown", V2EvaluationCohort>;
  model_cohorts?: V2ModelEvaluationCohort[];
  model_cohort_selection_rule?: string;
};

export type JevLearningStatus = {
  status: "collecting" | "ready";
  symbol: string | null;
  required_mature_roots: number;
  validation_fraction: number;
  minimum_validation_months: number;
  observed_mature_roots: number;
  observed_forecast_roots: number;
  observed_pending_roots: number;
  historical_replay_mature_roots_excluded: number;
  observed_roots_missing_raw_probabilities: number;
  cohorts: Array<{
    cohort_key: string;
    provider: string;
    actual_model: string;
    question_version: string;
    target_spec_version: string | null;
    forecast_roots: number;
    pending_roots: number;
    symbol_forecast_counts: Record<string, number>;
    matured_roots: number;
    training: {
      ready: boolean;
      matured_roots: number;
      required_roots: number;
      training_roots: number;
      validation_roots: number;
      training_months: number;
      validation_months: number;
      training_class_counts: Record<string, number>;
      validation_class_counts: Record<string, number>;
      reasons: string[];
    };
    active_model: null | {
      id: string;
      activated_at: string | null;
      sample_count: number;
      parameters_sha256: string;
      test_metrics: Record<string, unknown> | null;
      acceptance: Record<string, unknown> | null;
    };
  }>;
  policy: string;
};

export type V2Workspace = {
  symbol: string;
  status: "empty" | "available";
  current_root: { id: string; target_contract: Record<string, unknown> } | null;
  current_version: Pick<V2Version,
    "id" | "version_no" | "decision_at" | "market_cutoff_at" | "model_status" | "baseline_probabilities" | "joint_probabilities" | "decision_probabilities" | "research_brief"
  > | null;
  pending_job_count: number;
  joint_model_status: "unavailable" | "research_only" | "experimental_joint" | "experimental_jev";
  monitor_status: "not_recorded";
};
