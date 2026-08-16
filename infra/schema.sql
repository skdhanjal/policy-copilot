-- Phase 1 schema, v2.
--
-- Built against real eCFR data (scripts/probe_corpus.py output), not
-- assumption. Two decisions below exist BECAUSE of what the probe showed,
-- not despite it -- see the comments at each.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE TABLE policy_family (
    id            TEXT PRIMARY KEY,       -- e.g. 'cfr-16-314'
    title         TEXT NOT NULL,
    cfr_title     INT NOT NULL,           -- e.g. 16
    cfr_part      TEXT NOT NULL,          -- e.g. '314'
    jurisdiction  TEXT NOT NULL,          -- 'SEC' | 'FTC' | 'CFPB'
    -- Recorded because our own corpus selection was iterative and fallible
    -- (17 CFR 248, 240, 232 were all probed and rejected -- see DESIGN.md
    -- ADR-8). A future re-evaluation should not have to re-derive why a
    -- family was chosen from git blame.
    data_source   TEXT NOT NULL DEFAULT 'ecfr.gov',
    retired_on    DATE
);

CREATE TABLE policy_version (
    id              TEXT PRIMARY KEY,
    family_id       TEXT NOT NULL REFERENCES policy_family(id),
    -- DERIVED FROM effective_from, NEVER from a Federal Register citation.
    -- 16 CFR 314's probe returned FIVE eCFR amendment dates
    -- (2021-12-09, 2022-01-07, 2022-11-23, 2023-11-13, 2024-05-13) against
    -- only TWO expected FR rulemakings -- a single rule phases sections in on
    -- different dates. Labelling by FR citation would collapse distinct,
    -- individually-answerable version boundaries into one.
    version_label   TEXT NOT NULL,        -- e.g. 'v-2022-01-07'
    effective_from  DATE NOT NULL,
    effective_to    DATE,                 -- NULL = still in force
    supersedes      TEXT REFERENCES policy_version(id),
    -- Provenance, kept separate from version_label for the reason above.
    -- Multiple versions of one family may legitimately share an fr_citation.
    fr_citation     TEXT,
    source_uri      TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    CONSTRAINT valid_interval CHECK (effective_to IS NULL OR effective_to > effective_from)
);

-- Two versions of one family cannot be in force simultaneously. A data-model
-- bug here surfaces three weeks later as a hallucination with no stack trace.
ALTER TABLE policy_version ADD CONSTRAINT no_overlapping_versions
    EXCLUDE USING gist (
        family_id WITH =,
        daterange(effective_from, effective_to) WITH &&
    );

CREATE TABLE chunk (
    id           TEXT PRIMARY KEY,
    version_id   TEXT NOT NULL REFERENCES policy_version(id) ON DELETE CASCADE,
    ordinal      INT NOT NULL,
    -- The join key for diachronic comparison. Confirmed low-risk: CFR section
    -- numbers are stable across amendments by regulatory convention -- this is
    -- why the corpus was chosen, not an assumption we're still testing.
    section_path TEXT NOT NULL,           -- e.g. '275.206(4)-1'
    text         TEXT NOT NULL,
    token_count  INT NOT NULL,
    embedding    vector(384) NOT NULL
);

CREATE INDEX chunk_embedding_idx ON chunk
    USING hnsw (embedding vector_cosine_ops);
CREATE INDEX chunk_version_section_idx ON chunk (version_id, section_path);
CREATE INDEX version_family_dates_idx ON policy_version (family_id, effective_from, effective_to);

-- Stage 1 of retrieval searches ONLY this view -- current, in-force content.
-- This is what prevents near-duplicate versions from crowding the top-k
-- (ADR-2). 17 CFR 275 is a live test of this: 33 amendment dates across 26
-- sections means a naive all-versions search would let one policy's history
-- flood the top-k and evict FTC/CFPB results entirely.
CREATE VIEW current_chunk AS
SELECT c.*, v.family_id, v.effective_from, v.effective_to, f.jurisdiction, f.title
FROM chunk c
JOIN policy_version v ON v.id = c.version_id
JOIN policy_family  f ON f.id = v.family_id
WHERE v.effective_to IS NULL AND f.retired_on IS NULL;

-- Seed data: the three confirmed families (ADR-8 v3). Populated by ingest;
-- listed here so the schema file itself documents the final corpus without
-- needing DESIGN.md open side by side.
--
-- All three sit in financial-services regulatory compliance -- adviser
-- conduct, safeguarding customer data, consumer financial protection -- a
-- deliberate pivot from an earlier four-family HIPAA/FTC/CFPB/SEC mix
-- (see DESIGN.md ADR-8 v1/v2) once the person building this system found
-- they had no working mental model for US healthcare privacy law and
-- couldn't sanity-check the outputs. Financial-services compliance maps
-- conceptually to SEBI-regulated activity they already reason about.
INSERT INTO policy_family (id, title, cfr_title, cfr_part, jurisdiction) VALUES
    ('cfr-17-275',  'Rules and Regulations, Investment Advisers Act of 1940', 17, '275', 'SEC'),
    ('cfr-16-314',  'FTC Standards for Safeguarding Customer Information', 16, '314', 'FTC'),
    ('cfr-12-1005', 'Regulation E - Electronic Fund Transfers', 12, '1005', 'CFPB');