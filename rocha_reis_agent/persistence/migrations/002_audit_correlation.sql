ALTER TABLE audit ADD COLUMN correlation TEXT NOT NULL DEFAULT 'migration-v2';
CREATE INDEX audit_recorded_at ON audit(recorded_at);
CREATE INDEX conversations_updated_at ON conversations(updated_at);
CREATE INDEX events_processed_at ON events(processed_at);
