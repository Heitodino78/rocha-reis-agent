-- Fase 10: configurações operacionais tipadas do painel (preserva os valores existentes).
-- urgent_after_minutes: minutos de espera até a conversa ser destacada como urgente (1–1440).
-- retired_contact_policy: 0 = bloquear (comportamento anterior); 1 = permitir nova conversa.
--   A decisão é do controlador dos dados; o padrão continua sendo o bloqueio.
CREATE TABLE panel_settings_new (
    key TEXT PRIMARY KEY CHECK(key IN ('operators_can_transfer','operators_can_resume',
                                       'urgent_after_minutes','retired_contact_policy')),
    value INTEGER NOT NULL CHECK(value BETWEEN 0 AND 1440),
    updated_at TEXT NOT NULL,
    updated_by TEXT REFERENCES panel_users(id)
) STRICT;
INSERT INTO panel_settings_new(key,value,updated_at,updated_by) SELECT key,value,updated_at,updated_by FROM panel_settings;
DROP TABLE panel_settings;
ALTER TABLE panel_settings_new RENAME TO panel_settings;
-- Consultas do painel: transições por estado (tempo de espera) e mensagens do cliente (não lidas).
CREATE INDEX transitions_conversation_current ON transitions(conversation_id, current, ordinal);
CREATE INDEX messages_conversation_role ON messages(conversation_id, role, ordinal)
