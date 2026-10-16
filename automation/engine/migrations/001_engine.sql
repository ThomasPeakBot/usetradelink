-- SEO research engine schema. Collector tables (seo_runs, seo_pages,
-- seo_search_performance, seo_semrush_*) and approval tables are owned by
-- their services; this migration only adds engine tables.

CREATE TABLE IF NOT EXISTS engine_runs (
  id uuid PRIMARY KEY,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  status text NOT NULL DEFAULT 'running',
  steps jsonb NOT NULL DEFAULT '{}'::jsonb,
  errors jsonb NOT NULL DEFAULT '[]'::jsonb
);

-- Keyword metrics from any provider, one row per keyword/source/day.
CREATE TABLE IF NOT EXISTS seo_keyword_metrics (
  keyword text NOT NULL,
  database text NOT NULL DEFAULT 'us',
  source text NOT NULL,
  observed_on date NOT NULL,
  search_volume integer,
  keyword_difficulty numeric,
  cpc numeric,
  cpc_unit text,
  competition numeric,
  intents text[] NOT NULL DEFAULT '{}',
  serp_features text,
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (keyword, database, source, observed_on)
);

-- Questions and related terms as returned by a provider (no generated terms).
CREATE TABLE IF NOT EXISTS seo_keyword_relations (
  seed text NOT NULL,
  keyword text NOT NULL,
  relation text NOT NULL,
  source text NOT NULL,
  observed_on date NOT NULL,
  PRIMARY KEY (seed, keyword, relation, source, observed_on)
);

CREATE TABLE IF NOT EXISTS seo_competitors (
  domain text PRIMARY KEY,
  name text NOT NULL,
  active boolean NOT NULL DEFAULT true,
  notes text NOT NULL DEFAULT ''
);

-- Organic rankings of competitors, exactly as reported by the provider.
CREATE TABLE IF NOT EXISTS seo_competitor_rankings (
  domain text NOT NULL,
  keyword text NOT NULL,
  position integer,
  url text,
  traffic numeric,
  traffic_share numeric,
  search_volume integer,
  source text NOT NULL,
  observed_on date NOT NULL,
  PRIMARY KEY (domain, keyword, source, observed_on)
);

-- Snapshots of public competitor pages: extracted structure only, with URL and time.
CREATE TABLE IF NOT EXISTS seo_competitor_pages (
  url text NOT NULL,
  domain text NOT NULL,
  fetched_at timestamptz NOT NULL DEFAULT now(),
  http_status integer,
  title text,
  meta_description text,
  h1 text,
  headings jsonb NOT NULL DEFAULT '[]'::jsonb,
  word_count integer,
  content_hash text,
  PRIMARY KEY (url, fetched_at)
);

-- Our own pages as published (or as in the repository for local runs).
CREATE TABLE IF NOT EXISTS seo_page_inventory (
  url text NOT NULL,
  inventoried_at timestamptz NOT NULL DEFAULT now(),
  title text,
  meta_description text,
  h1 text,
  headings jsonb NOT NULL DEFAULT '[]'::jsonb,
  word_count integer,
  internal_links jsonb NOT NULL DEFAULT '[]'::jsonb,
  date_modified date,
  content_hash text,
  PRIMARY KEY (url, inventoried_at)
);

CREATE TABLE IF NOT EXISTS seo_opportunities (
  id uuid PRIMARY KEY,
  fingerprint text NOT NULL,
  run_id uuid REFERENCES engine_runs(id),
  action text NOT NULL,
  content_type text,
  target_url text,
  head_keyword text NOT NULL,
  keywords text[] NOT NULL DEFAULT '{}',
  priority numeric NOT NULL,
  score jsonb NOT NULL DEFAULT '{}'::jsonb,
  evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
  rationale text NOT NULL,
  status text NOT NULL DEFAULT 'open',
  first_seen timestamptz NOT NULL DEFAULT now(),
  last_seen timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS seo_opportunities_open_fingerprint
  ON seo_opportunities (fingerprint) WHERE status IN ('open', 'briefed', 'drafted', 'proposed');

CREATE TABLE IF NOT EXISTS seo_content_briefs (
  id uuid PRIMARY KEY,
  opportunity_id uuid NOT NULL REFERENCES seo_opportunities(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  brief jsonb NOT NULL,
  markdown text NOT NULL
);

CREATE TABLE IF NOT EXISTS seo_content_drafts (
  id uuid PRIMARY KEY,
  brief_id uuid NOT NULL REFERENCES seo_content_briefs(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  path text NOT NULL,
  html text NOT NULL,
  quality jsonb NOT NULL,
  status text NOT NULL DEFAULT 'draft',
  pr_number integer
);

-- Published website changes and their measured outcomes.
CREATE TABLE IF NOT EXISTS seo_changes (
  id bigserial PRIMARY KEY,
  proposal_id uuid,
  pr_number integer,
  merged_sha text,
  pages text[] NOT NULL,
  change_type text NOT NULL DEFAULT 'content',
  published_at timestamptz NOT NULL,
  UNIQUE (proposal_id)
);

CREATE TABLE IF NOT EXISTS seo_change_evaluations (
  change_id bigint NOT NULL REFERENCES seo_changes(id),
  evaluated_at timestamptz NOT NULL DEFAULT now(),
  window_days integer NOT NULL,
  before jsonb NOT NULL,
  after jsonb NOT NULL,
  verdict text NOT NULL,
  PRIMARY KEY (change_id, window_days)
);

-- Every paid provider call, with the units the provider reported when it reports them.
CREATE TABLE IF NOT EXISTS engine_api_usage (
  id bigserial PRIMARY KEY,
  requested_at timestamptz NOT NULL DEFAULT now(),
  provider text NOT NULL,
  report text NOT NULL,
  target text NOT NULL,
  units_reserved integer NOT NULL,
  units_reported integer,
  rows_returned integer,
  success boolean,
  error_category text
);
