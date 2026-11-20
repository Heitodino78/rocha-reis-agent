CREATE TABLE channel_control (
    key TEXT PRIMARY KEY CHECK(key IN ('dispatch_halt')),
    reason TEXT NOT NULL CHECK(length(reason) BETWEEN 1 AND 200),
    set_by TEXT NOT NULL CHECK(length(set_by) BETWEEN 1 AND 64),
    set_at TEXT NOT NULL
) STRICT;
CREATE TABLE channel_send_ledger (
    id INTEGER PRIMARY KEY,
    outbox_id TEXT NOT NULL REFERENCES channel_outbox(id),
    authorization_id TEXT NOT NULL CHECK(length(authorization_id) BETWEEN 1 AND 64),
    run_id TEXT NOT NULL CHECK(length(run_id) = 32 AND run_id NOT GLOB '*[^0-9a-f]*'),
    recipient_hash TEXT NOT NULL CHECK(length(recipient_hash) = 64 AND recipient_hash NOT GLOB '*[^0-9a-f]*'),
    attempted_at TEXT NOT NULL
) STRICT;
CREATE INDEX channel_send_ledger_authorization ON channel_send_ledger(authorization_id, attempted_at);
CREATE INDEX channel_send_ledger_recipient ON channel_send_ledger(authorization_id, recipient_hash);
CREATE TABLE channel_metrics (
    name TEXT PRIMARY KEY CHECK(length(name) BETWEEN 1 AND 64 AND name NOT GLOB '*[^a-z0-9_]*'),
    value INTEGER NOT NULL CHECK(value >= 0)
) STRICT
