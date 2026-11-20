-- Fase 13: segundo fator (TOTP) do painel. O segredo TOTP precisa ser recuperável para verificar
-- códigos, então é guardado CIFRADO (AES-256-GCM, chave fora do banco); nunca em texto.
CREATE TABLE panel_mfa (
    user_id TEXT PRIMARY KEY REFERENCES panel_users(id),
    state TEXT NOT NULL CHECK(state IN ('pending','active')),
    secret_blob TEXT NOT NULL CHECK(length(secret_blob) BETWEEN 40 AND 400),
    key_id TEXT NOT NULL CHECK(length(key_id) = 16 AND key_id NOT GLOB '*[^0-9a-f]*'),
    last_step INTEGER NOT NULL DEFAULT 0 CHECK(last_step >= 0),
    created_at TEXT NOT NULL,
    activated_at TEXT
) STRICT;
-- Códigos de recuperação: somente hash (alta entropia), uso único.
CREATE TABLE panel_recovery_codes (
    id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES panel_users(id),
    code_hash TEXT NOT NULL CHECK(length(code_hash) = 64 AND code_hash NOT GLOB '*[^0-9a-f]*'),
    created_at TEXT NOT NULL,
    used_at TEXT,
    UNIQUE(user_id, code_hash)
) STRICT;
-- Etapa intermediária do login (senha correta, segundo fator pendente). NÃO é sessão.
CREATE TABLE panel_mfa_challenges (
    token_hash TEXT PRIMARY KEY CHECK(length(token_hash) = 64),
    user_id TEXT NOT NULL REFERENCES panel_users(id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    consumed_at TEXT
) STRICT;
CREATE INDEX panel_mfa_challenges_user ON panel_mfa_challenges(user_id);
-- Redefinição administrativa do segundo fator: justificativa obrigatória (registro separado da auditoria).
CREATE TABLE panel_mfa_resets (
    id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES panel_users(id),
    actor_user_id TEXT NOT NULL REFERENCES panel_users(id),
    justification TEXT NOT NULL CHECK(length(justification) BETWEEN 10 AND 300),
    at TEXT NOT NULL
) STRICT
