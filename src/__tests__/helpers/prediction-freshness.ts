export const freshPrediction = {
  schema_version: 'prediction-freshness-v1' as const,
  generated_at: new Date().toISOString(), data_revision: 'db-1', model_runtime_revision: 'model-1',
  resolved_model_id: 'current', source: 'saved_db' as const, cache_hit: false, data_changed_during_prediction: false,
  capabilities: { saved_db_recompute: true },
  source_tracking: true, source_snapshot_verified: false as const, revision_kind: 'sqlite-source-generation' as const,
}
