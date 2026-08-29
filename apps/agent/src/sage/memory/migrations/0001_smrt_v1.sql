CREATE SCHEMA IF NOT EXISTS sage_smrt;

CREATE TABLE IF NOT EXISTS sage_smrt.schema_migrations (
    version text PRIMARY KEY,
    checksum char(64) NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sage_smrt.repositories (
    repository_id uuid PRIMARY KEY,
    namespace_kind text NOT NULL CHECK (namespace_kind IN ('github', 'local')),
    namespace_key text NOT NULL,
    display_name text NOT NULL,
    latest_ready_snapshot_id uuid NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (namespace_kind, namespace_key),
    UNIQUE (repository_id, latest_ready_snapshot_id)
);

CREATE TABLE IF NOT EXISTS sage_smrt.semantic_objects (
    repository_id uuid NOT NULL REFERENCES sage_smrt.repositories(repository_id) ON DELETE CASCADE,
    semantic_digest char(64) NOT NULL,
    payload_digest char(64) NOT NULL,
    node_type text NOT NULL CHECK (node_type IN ('file', 'directory')),
    source_oid varchar(64) NOT NULL,
    semantic_payload jsonb NOT NULL,
    structure jsonb NULL,
    schema_version text NOT NULL,
    summarizer_provider text NOT NULL,
    summarizer_model text NOT NULL,
    prompt_version text NOT NULL,
    parser_version text NULL,
    generation_mode text NOT NULL CHECK (generation_mode IN ('full', 'delta')),
    delta_depth integer NOT NULL CHECK (delta_depth >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (repository_id, semantic_digest),
    CHECK ((node_type = 'file' AND structure IS NOT NULL) OR
           (node_type = 'directory' AND structure IS NULL))
);
CREATE INDEX IF NOT EXISTS semantic_source_idx
    ON sage_smrt.semantic_objects(repository_id, node_type, source_oid);
CREATE INDEX IF NOT EXISTS semantic_payload_idx
    ON sage_smrt.semantic_objects(repository_id, payload_digest);

CREATE OR REPLACE FUNCTION sage_smrt.reject_immutable_update()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'canonical memory objects are immutable'
      USING ERRCODE = '55000';
END;
$$;

CREATE TABLE IF NOT EXISTS sage_smrt.semantic_dependencies (
    repository_id uuid NOT NULL,
    parent_digest char(64) NOT NULL,
    child_order integer NOT NULL CHECK (child_order >= 0),
    child_name text NOT NULL,
    child_digest char(64) NOT NULL,
    PRIMARY KEY (repository_id, parent_digest, child_order),
    UNIQUE (repository_id, parent_digest, child_name),
    FOREIGN KEY (repository_id, parent_digest)
      REFERENCES sage_smrt.semantic_objects(repository_id, semantic_digest) ON DELETE CASCADE,
    FOREIGN KEY (repository_id, child_digest)
      REFERENCES sage_smrt.semantic_objects(repository_id, semantic_digest)
);

CREATE TRIGGER semantic_objects_immutable
BEFORE UPDATE ON sage_smrt.semantic_objects
FOR EACH ROW EXECUTE FUNCTION sage_smrt.reject_immutable_update();

CREATE TRIGGER semantic_dependencies_immutable
BEFORE UPDATE ON sage_smrt.semantic_dependencies
FOR EACH ROW EXECUTE FUNCTION sage_smrt.reject_immutable_update();

CREATE TABLE IF NOT EXISTS sage_smrt.overlay_nodes (
    repository_id uuid NOT NULL REFERENCES sage_smrt.repositories(repository_id) ON DELETE CASCADE,
    overlay_digest char(64) NOT NULL,
    node_type text NOT NULL CHECK (node_type IN ('file', 'directory')),
    source_oid varchar(64) NOT NULL,
    semantic_digest char(64) NULL,
    stale_hint_digest char(64) NULL,
    semantic_state text NOT NULL CHECK (semantic_state IN ('valid', 'stale', 'missing')),
    coverage_state text NULL CHECK (coverage_state IN ('partial', 'complete')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (repository_id, overlay_digest),
    FOREIGN KEY (repository_id, semantic_digest)
      REFERENCES sage_smrt.semantic_objects(repository_id, semantic_digest),
    FOREIGN KEY (repository_id, stale_hint_digest)
      REFERENCES sage_smrt.semantic_objects(repository_id, semantic_digest),
    CHECK ((semantic_state = 'valid' AND semantic_digest IS NOT NULL) OR
           (semantic_state <> 'valid' AND semantic_digest IS NULL)),
    CHECK (semantic_state <> 'missing' OR stale_hint_digest IS NULL),
    CHECK ((node_type = 'file' AND coverage_state IS NULL) OR node_type = 'directory')
);

CREATE TABLE IF NOT EXISTS sage_smrt.overlay_edges (
    repository_id uuid NOT NULL,
    parent_overlay_digest char(64) NOT NULL,
    child_name text NOT NULL CHECK (
      child_name <> '' AND child_name NOT IN ('.', '..')
      AND position('/' in child_name) = 0 AND position(chr(92) in child_name) = 0
    ),
    child_overlay_digest char(64) NOT NULL,
    child_order integer NOT NULL CHECK (child_order >= 0),
    PRIMARY KEY (repository_id, parent_overlay_digest, child_name),
    UNIQUE (repository_id, parent_overlay_digest, child_order),
    FOREIGN KEY (repository_id, parent_overlay_digest)
      REFERENCES sage_smrt.overlay_nodes(repository_id, overlay_digest) ON DELETE CASCADE,
    FOREIGN KEY (repository_id, child_overlay_digest)
      REFERENCES sage_smrt.overlay_nodes(repository_id, overlay_digest)
);

CREATE TRIGGER overlay_nodes_immutable
BEFORE UPDATE ON sage_smrt.overlay_nodes
FOR EACH ROW EXECUTE FUNCTION sage_smrt.reject_immutable_update();

CREATE TRIGGER overlay_edges_immutable
BEFORE UPDATE ON sage_smrt.overlay_edges
FOR EACH ROW EXECUTE FUNCTION sage_smrt.reject_immutable_update();

CREATE TABLE IF NOT EXISTS sage_smrt.snapshots (
    snapshot_id uuid PRIMARY KEY,
    repository_id uuid NOT NULL REFERENCES sage_smrt.repositories(repository_id) ON DELETE CASCADE,
    parent_snapshot_id uuid NULL,
    target_commit_oid varchar(64) NOT NULL,
    target_root_tree_oid varchar(64) NOT NULL,
    root_overlay_digest char(64) NULL,
    status text NOT NULL CHECK (status IN ('BUILDING', 'READY', 'FAILED')),
    run_id text NOT NULL,
    schema_version text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    ready_at timestamptz NULL,
    failure_code text NULL,
    UNIQUE (repository_id, snapshot_id),
    FOREIGN KEY (repository_id, parent_snapshot_id)
      REFERENCES sage_smrt.snapshots(repository_id, snapshot_id),
    FOREIGN KEY (repository_id, root_overlay_digest)
      REFERENCES sage_smrt.overlay_nodes(repository_id, overlay_digest),
    CHECK ((status = 'READY' AND ready_at IS NOT NULL AND root_overlay_digest IS NOT NULL) OR
           (status <> 'READY' AND ready_at IS NULL))
);
CREATE INDEX IF NOT EXISTS snapshot_latest_idx
    ON sage_smrt.snapshots(repository_id, status, ready_at DESC);
CREATE INDEX IF NOT EXISTS snapshot_target_idx
    ON sage_smrt.snapshots(repository_id, target_commit_oid);
CREATE INDEX IF NOT EXISTS snapshot_building_idx
    ON sage_smrt.snapshots(status, created_at);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'repositories_latest_ready_fk'
    ) THEN
        ALTER TABLE sage_smrt.repositories
          ADD CONSTRAINT repositories_latest_ready_fk
          FOREIGN KEY (repository_id, latest_ready_snapshot_id)
          REFERENCES sage_smrt.snapshots(repository_id, snapshot_id);
    END IF;
END $$;
