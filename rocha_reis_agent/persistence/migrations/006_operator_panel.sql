-- Fase 7: painel de atendimento humano.
-- Executada com foreign_keys=OFF e foreign_key_check antes do commit (procedimento oficial SQLite).

ALTER TABLE messages ADD COLUMN operation_id TEXT CHECK(operation_id IS NULL OR (length(operation_id) = 32 AND operation_id NOT GLOB '*[^0-9a-f]*'));

-- Outbox reconstruída: respostas do operador não têm mensagem de origem na inbox.
CREATE TABLE channel_outbox_new (
    id TEXT PRIMARY KEY CHECK(length(id) = 32 AND id NOT GLOB '*[^0-9a-f]*'),
    channel TEXT NOT NULL CHECK(channel = 'whatsapp'),
    business_number_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    session_id TEXT NOT NULL,
    source_inbox_id INTEGER UNIQUE REFERENCES channel_inbox(id),
    source_event_id TEXT,
    required_state TEXT NOT NULL CHECK(required_state IN ('AUTO','WAITING_HUMAN','HUMAN_ACTIVE','RESOLVED')),
    recipient TEXT NOT NULL CHECK(json_valid(recipient) AND json_type(recipient) = 'object'),
    body TEXT NOT NULL CHECK(length(body) <= 4096),
    window_opened_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('PENDING','SENDING','RETRY','ACCEPTED','FAILED','UNKNOWN','SUPPRESSED')),
    outcome TEXT,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    next_attempt_at TEXT NOT NULL,
    lease_owner TEXT,
    lease_expires_at TEXT,
    provider_message_id TEXT UNIQUE,
    delivery_status TEXT CHECK(delivery_status IS NULL OR delivery_status IN ('sent','delivered','read','failed')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    delivery_error_code INTEGER,
    delivery_updated_at TEXT,
    status_anomalies INTEGER NOT NULL DEFAULT 0 CHECK(status_anomalies >= 0),
    origin TEXT NOT NULL DEFAULT 'agent' CHECK(origin IN ('agent','operator')),
    operator_user_id TEXT,
    idempotency_key TEXT UNIQUE CHECK(idempotency_key IS NULL OR (length(idempotency_key) BETWEEN 16 AND 64 AND idempotency_key NOT GLOB '*[^A-Za-z0-9_-]*')),
    CHECK((status = 'SENDING') = (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)),
    CHECK(status <> 'ACCEPTED' OR provider_message_id IS NOT NULL),
    CHECK((origin = 'agent' AND source_inbox_id IS NOT NULL AND source_event_id IS NOT NULL
           AND operator_user_id IS NULL AND idempotency_key IS NULL)
       OR (origin = 'operator' AND source_inbox_id IS NULL AND operator_user_id IS NOT NULL
           AND idempotency_key IS NOT NULL))
) STRICT;

INSERT INTO channel_outbox_new(id,channel,business_number_id,conversation_id,session_id,source_inbox_id,source_event_id,
    required_state,recipient,body,window_opened_at,status,outcome,attempts,next_attempt_at,lease_owner,lease_expires_at,
    provider_message_id,delivery_status,created_at,updated_at,delivery_error_code,delivery_updated_at,status_anomalies,origin)
SELECT id,channel,business_number_id,conversation_id,session_id,source_inbox_id,source_event_id,
    required_state,recipient,body,window_opened_at,status,outcome,attempts,next_attempt_at,lease_owner,lease_expires_at,
    provider_message_id,delivery_status,created_at,updated_at,delivery_error_code,delivery_updated_at,status_anomalies,'agent'
FROM channel_outbox ORDER BY rowid;

DROP TABLE channel_outbox;
ALTER TABLE channel_outbox_new RENAME TO channel_outbox;
CREATE INDEX channel_outbox_ready ON channel_outbox(status, next_attempt_at);
CREATE INDEX channel_outbox_conversation ON channel_outbox(conversation_id, status);

-- Endereço de resposta mais recente por conversa (necessário para a resposta humana).
-- Removido na exclusão/anonimização; nunca exibido no painel.
CREATE TABLE channel_contacts (
    conversation_id TEXT PRIMARY KEY REFERENCES conversations(id),
    business_number_id TEXT NOT NULL,
    recipient TEXT NOT NULL CHECK(json_valid(recipient) AND json_type(recipient) = 'object'),
    last_inbound_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
) STRICT;

-- Funcionários do painel. Senhas somente como hash Argon2id.
CREATE TABLE panel_users (
    id TEXT PRIMARY KEY CHECK(length(id) = 32 AND id NOT GLOB '*[^0-9a-f]*'),
    login TEXT NOT NULL UNIQUE CHECK(length(login) BETWEEN 3 AND 64 AND login NOT GLOB '*[^a-z0-9._-]*'),
    display_name TEXT NOT NULL CHECK(length(display_name) BETWEEN 1 AND 80),
    password_hash TEXT NOT NULL CHECK(password_hash LIKE '$argon2id$%'),
    role TEXT NOT NULL CHECK(role IN ('ADMIN','OPERATOR')),
    active INTEGER NOT NULL CHECK(active IN (0,1)),
    must_change_password INTEGER NOT NULL DEFAULT 0 CHECK(must_change_password IN (0,1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT
) STRICT;

-- Sessões: só o hash do token fica no banco.
CREATE TABLE panel_sessions (
    token_hash TEXT PRIMARY KEY CHECK(length(token_hash) = 64 AND token_hash NOT GLOB '*[^0-9a-f]*'),
    user_id TEXT NOT NULL REFERENCES panel_users(id),
    csrf_token TEXT NOT NULL CHECK(length(csrf_token) >= 32),
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT
) STRICT;
CREATE INDEX panel_sessions_user ON panel_sessions(user_id);

CREATE TABLE panel_login_attempts (
    id INTEGER PRIMARY KEY,
    key_hash TEXT NOT NULL CHECK(length(key_hash) = 64),
    attempted_at TEXT NOT NULL
) STRICT;
CREATE INDEX panel_login_attempts_key ON panel_login_attempts(key_hash, attempted_at);

-- Responsável atual por uma conversa em atendimento humano.
CREATE TABLE panel_assignments (
    conversation_id TEXT PRIMARY KEY REFERENCES conversations(id),
    user_id TEXT NOT NULL REFERENCES panel_users(id),
    assigned_at TEXT NOT NULL,
    assigned_by TEXT NOT NULL REFERENCES panel_users(id)
) STRICT;
CREATE INDEX panel_assignments_user ON panel_assignments(user_id);

CREATE TABLE panel_read_state (
    user_id TEXT NOT NULL REFERENCES panel_users(id),
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    read_at TEXT NOT NULL,
    PRIMARY KEY(user_id, conversation_id)
) STRICT;

CREATE TABLE panel_settings (
    key TEXT PRIMARY KEY CHECK(key IN ('operators_can_transfer','operators_can_resume')),
    value INTEGER NOT NULL CHECK(value IN (0,1)),
    updated_at TEXT NOT NULL,
    updated_by TEXT REFERENCES panel_users(id)
) STRICT;

-- Auditoria do painel: somente inclusão (UPDATE/DELETE abortados por trigger).
CREATE TABLE panel_audit (
    id INTEGER PRIMARY KEY,
    operation_id TEXT NOT NULL CHECK(length(operation_id) = 32),
    actor_user_id TEXT,
    action TEXT NOT NULL CHECK(length(action) BETWEEN 1 AND 64),
    entity_type TEXT NOT NULL CHECK(entity_type IN ('session','user','conversation','settings','dispatch')),
    entity_id TEXT,
    outcome TEXT NOT NULL CHECK(outcome IN ('success','denied','failed')),
    at TEXT NOT NULL
) STRICT;
CREATE INDEX panel_audit_at ON panel_audit(at);
CREATE TRIGGER panel_audit_no_update BEFORE UPDATE ON panel_audit
BEGIN SELECT RAISE(ABORT, 'panel_audit is append-only'); END;
CREATE TRIGGER panel_audit_no_delete BEFORE DELETE ON panel_audit
BEGIN SELECT RAISE(ABORT, 'panel_audit is append-only'); END;

-- Feed de mudanças persistido para o tempo real (SSE com cursor; sobrevive a reinícios).
CREATE TABLE change_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('conversation','outbox','assignment')),
    at TEXT NOT NULL
) STRICT;
CREATE TRIGGER change_conversation_insert AFTER INSERT ON conversations
BEGIN INSERT INTO change_log(conversation_id,kind,at) VALUES(NEW.id,'conversation',strftime('%Y-%m-%dT%H:%M:%fZ','now')); END;
CREATE TRIGGER change_conversation_update AFTER UPDATE ON conversations
BEGIN INSERT INTO change_log(conversation_id,kind,at) VALUES(NEW.id,'conversation',strftime('%Y-%m-%dT%H:%M:%fZ','now')); END;
CREATE TRIGGER change_outbox_insert AFTER INSERT ON channel_outbox
BEGIN INSERT INTO change_log(conversation_id,kind,at) VALUES(NEW.conversation_id,'outbox',strftime('%Y-%m-%dT%H:%M:%fZ','now')); END;
CREATE TRIGGER change_outbox_update AFTER UPDATE OF status, delivery_status ON channel_outbox
BEGIN INSERT INTO change_log(conversation_id,kind,at) VALUES(NEW.conversation_id,'outbox',strftime('%Y-%m-%dT%H:%M:%fZ','now')); END;
CREATE TRIGGER change_assignment_insert AFTER INSERT ON panel_assignments
BEGIN INSERT INTO change_log(conversation_id,kind,at) VALUES(NEW.conversation_id,'assignment',strftime('%Y-%m-%dT%H:%M:%fZ','now')); END;
CREATE TRIGGER change_assignment_update AFTER UPDATE ON panel_assignments
BEGIN INSERT INTO change_log(conversation_id,kind,at) VALUES(NEW.conversation_id,'assignment',strftime('%Y-%m-%dT%H:%M:%fZ','now')); END;
CREATE TRIGGER change_assignment_delete AFTER DELETE ON panel_assignments
BEGIN INSERT INTO change_log(conversation_id,kind,at) VALUES(OLD.conversation_id,'assignment',strftime('%Y-%m-%dT%H:%M:%fZ','now')); END
