-- OMN-12491: reconcile the vestigial omnidash_analytics.savings_estimates table to the
-- canonical omnimarket schema (migrations 074 + 075). Idempotent. Applied 2026-05-30 on
-- the stability-test lane (omnibase-infra-stability-test-postgres, db omnidash_analytics).
--
-- NOTE: the RUNTIME and projection API use omnibase_infra.savings_estimates, not this
-- table. This DDL only prevents the divergent omnidash_analytics copy from silently
-- mis-routing any future writer; it does not affect demo data.

BEGIN;

ALTER TABLE savings_estimates
    ADD COLUMN IF NOT EXISTS repo_name TEXT,
    ADD COLUMN IF NOT EXISTS machine_id TEXT,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE UNIQUE INDEX IF NOT EXISTS ux_savings_estimates_identity
    ON savings_estimates (
        session_id,
        event_timestamp,
        model_local,
        model_cloud_baseline
    );

CREATE OR REPLACE FUNCTION refresh_savings_estimates_updated_at()
RETURNS TRIGGER AS $func$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$func$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_savings_estimates_updated_at ON savings_estimates;
CREATE TRIGGER trg_savings_estimates_updated_at
    BEFORE UPDATE ON savings_estimates
    FOR EACH ROW
    EXECUTE FUNCTION refresh_savings_estimates_updated_at();

COMMIT;
