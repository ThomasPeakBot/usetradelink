-- Operator switch shared by the collector and the engine: set paused=true to stop all
-- Semrush spending without a redeploy. Both services also set it automatically when a
-- call costs more than its estimate.
CREATE TABLE IF NOT EXISTS seo_spend_controls (
  provider text PRIMARY KEY,
  paused boolean NOT NULL DEFAULT false,
  reason text NOT NULL DEFAULT '',
  updated_at timestamptz NOT NULL DEFAULT now()
);
