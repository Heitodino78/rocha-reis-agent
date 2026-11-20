CREATE TABLE channel_inbox (
    id INTEGER PRIMARY KEY,
    channel TEXT NOT NULL CHECK(channel = 'whatsapp'),
    business_number_id TEXT NOT NULL CHECK(length(business_number_id) BETWEEN 1 AND 32 AND business_number_id NOT GLOB '*[^0-9]*'),
    dedup_key TEXT NOT NULL CHECK(length(dedup_key) BETWEEN 8 AND 96),
    kind TEXT NOT NULL CHECK(kind IN ('message','status','unsupported')),
    digest TEXT NOT NULL CHECK(length(digest) = 64 AND digest NOT GLOB '*[^0-9a-f]*'),
    payload TEXT NOT NULL CHECK(json_valid(payload) AND json_type(payload) = 'object' AND length(payload) <= 16384),
    status TEXT NOT NULL CHECK(status IN ('PENDING','PROCESSING','DONE','FAILED')),
    outcome TEXT,
    conversation_id TEXT CHECK(conversation_id IS NULL OR (length(conversation_id) BETWEEN 1 AND 64 AND conversation_id NOT GLOB '*[^A-Za-z0-9_-]*')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    next_attempt_at TEXT NOT NULL,
    lease_owner TEXT,
    lease_expires_at TEXT,
    source_timestamp TEXT NOT NULL,
    received_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(channel, business_number_id, dedup_key),
    CHECK((status = 'PROCESSING') = (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL))
) STRICT;
CREATE INDEX channel_inbox_ready ON channel_inbox(status, next_attempt_at);
CREATE INDEX channel_inbox_conversation ON channel_inbox(conversation_id, status);
CREATE TABLE channel_outbox (
    id TEXT PRIMARY KEY CHECK(length(id) = 32 AND id NOT GLOB '*[^0-9a-f]*'),
    channel TEXT NOT NULL CHECK(channel = 'whatsapp'),
    business_number_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    session_id TEXT NOT NULL,
    source_inbox_id INTEGER NOT NULL UNIQUE REFERENCES channel_inbox(id),
    source_event_id TEXT NOT NULL,
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
    CHECK((status = 'SENDING') = (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)),
    CHECK(status <> 'ACCEPTED' OR provider_message_id IS NOT NULL)
) STRICT;
CREATE INDEX channel_outbox_ready ON channel_outbox(status, next_attempt_at);
CREATE INDEX channel_outbox_conversation ON channel_outbox(conversation_id, status)
