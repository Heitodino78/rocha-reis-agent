-- Fase 8: base de conhecimento durável (revisões imutáveis) e consumo da IA.

-- Cada linha é UMA revisão FactRevision completa (JSON validado pelo modelo de domínio).
-- Somente inclusão: aprovar, revogar ou arquivar cria nova revisão; nada é sobrescrito.
CREATE TABLE knowledge_revisions (
    id INTEGER PRIMARY KEY,
    fact_id TEXT NOT NULL CHECK(length(fact_id) BETWEEN 1 AND 80),
    version INTEGER NOT NULL CHECK(version >= 1),
    status TEXT NOT NULL CHECK(status IN ('DRAFT','APPROVED','REJECTED','ARCHIVED')),
    scope TEXT NOT NULL CHECK(scope IN ('SYNTHETIC','OFFICIAL')),
    document TEXT NOT NULL CHECK(json_valid(document) AND json_type(document) = 'object'),
    author TEXT NOT NULL CHECK(length(author) BETWEEN 1 AND 80),
    created_at TEXT NOT NULL,
    UNIQUE(fact_id, version)
) STRICT;
CREATE TRIGGER knowledge_revisions_no_update BEFORE UPDATE ON knowledge_revisions
BEGIN SELECT RAISE(ABORT, 'knowledge_revisions is append-only'); END;
CREATE TRIGGER knowledge_revisions_no_delete BEFORE DELETE ON knowledge_revisions
BEGIN SELECT RAISE(ABORT, 'knowledge_revisions is append-only'); END;

-- Consumo do provedor de IA: somente metadados técnicos (nunca texto, prompt ou resposta).
CREATE TABLE ai_usage (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    provider TEXT NOT NULL CHECK(length(provider) BETWEEN 1 AND 32),
    model TEXT NOT NULL CHECK(length(model) BETWEEN 1 AND 80),
    prompt_version TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK(length(outcome) BETWEEN 1 AND 40),
    input_tokens INTEGER NOT NULL DEFAULT 0 CHECK(input_tokens >= 0),
    output_tokens INTEGER NOT NULL DEFAULT 0 CHECK(output_tokens >= 0),
    cache_read_tokens INTEGER NOT NULL DEFAULT 0 CHECK(cache_read_tokens >= 0),
    cache_write_tokens INTEGER NOT NULL DEFAULT 0 CHECK(cache_write_tokens >= 0),
    latency_ms INTEGER NOT NULL DEFAULT 0 CHECK(latency_ms >= 0),
    -- Estimativa em micro-dólares a partir de tabela configurada; NULL = tarifa desconhecida.
    estimated_cost_micros INTEGER CHECK(estimated_cost_micros IS NULL OR estimated_cost_micros >= 0)
) STRICT;
CREATE INDEX ai_usage_at ON ai_usage(at)
;

-- Auditoria do painel passa a aceitar a entidade 'knowledge' (reconstrução preservando os registros).
CREATE TABLE panel_audit_new (
    id INTEGER PRIMARY KEY,
    operation_id TEXT NOT NULL CHECK(length(operation_id) = 32),
    actor_user_id TEXT,
    action TEXT NOT NULL CHECK(length(action) BETWEEN 1 AND 64),
    entity_type TEXT NOT NULL CHECK(entity_type IN ('session','user','conversation','settings','dispatch','knowledge')),
    entity_id TEXT,
    outcome TEXT NOT NULL CHECK(outcome IN ('success','denied','failed')),
    at TEXT NOT NULL
) STRICT;
INSERT INTO panel_audit_new SELECT id,operation_id,actor_user_id,action,entity_type,entity_id,outcome,at FROM panel_audit ORDER BY id;
DROP TABLE panel_audit;
ALTER TABLE panel_audit_new RENAME TO panel_audit;
CREATE INDEX panel_audit_at ON panel_audit(at);
CREATE TRIGGER panel_audit_no_update BEFORE UPDATE ON panel_audit
BEGIN SELECT RAISE(ABORT, 'panel_audit is append-only'); END;
CREATE TRIGGER panel_audit_no_delete BEFORE DELETE ON panel_audit
BEGIN SELECT RAISE(ABORT, 'panel_audit is append-only'); END
