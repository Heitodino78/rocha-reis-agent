ALTER TABLE conversations ADD COLUMN archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1));
ALTER TABLE conversations ADD COLUMN anonymized_at TEXT;
CREATE INDEX conversations_active ON conversations(retired, archived, updated_at);
CREATE INDEX events_conversation_processed ON events(conversation_id, processed_at);
ALTER TABLE channel_outbox ADD COLUMN delivery_error_code INTEGER;
ALTER TABLE channel_outbox ADD COLUMN delivery_updated_at TEXT;
ALTER TABLE channel_outbox ADD COLUMN status_anomalies INTEGER NOT NULL DEFAULT 0 CHECK(status_anomalies >= 0)
