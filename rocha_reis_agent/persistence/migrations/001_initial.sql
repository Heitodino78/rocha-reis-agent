CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL) STRICT;
INSERT INTO metadata VALUES ('purpose', 'rocha-reis-synthetic-only');
CREATE TABLE conversations (
    id TEXT PRIMARY KEY CHECK(length(id) BETWEEN 1 AND 64 AND id NOT GLOB '*[^A-Za-z0-9_-]*'),
    session_id TEXT NOT NULL CHECK(length(session_id) BETWEEN 1 AND 64),
    client_id TEXT NOT NULL CHECK(client_id = id),
    state TEXT NOT NULL CHECK(state IN ('AUTO','WAITING_HUMAN','HUMAN_ACTIVE','RESOLVED')),
    closed INTEGER NOT NULL CHECK(closed IN (0,1)),
    retired INTEGER NOT NULL DEFAULT 0 CHECK(retired IN (0,1)),
    version INTEGER NOT NULL CHECK(version >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK(retired = 0 OR (closed = 1 AND state = 'RESOLVED'))
) STRICT;
CREATE TABLE context (
    conversation_id TEXT PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
    services TEXT NOT NULL CHECK(json_valid(services) AND json_type(services) = 'array'),
    age INTEGER NOT NULL CHECK(age >= 0)
) STRICT;
CREATE TABLE events (
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    event_id TEXT NOT NULL CHECK(length(event_id) BETWEEN 1 AND 64 AND event_id NOT GLOB '*[^A-Za-z0-9_-]*'),
    session_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind = 'client_message'),
    status TEXT NOT NULL CHECK(status = 'COMPLETED'),
    digest TEXT NOT NULL CHECK(length(digest) = 64 AND digest NOT GLOB '*[^0-9a-f]*'),
    result TEXT NOT NULL CHECK(json_valid(result) AND json_type(result) = 'object'),
    processed_at TEXT NOT NULL,
    PRIMARY KEY(conversation_id, event_id)
) STRICT;
CREATE TABLE messages (
    id INTEGER PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
    event_id TEXT,
    role TEXT NOT NULL CHECK(role IN ('client','assistant','operator')),
    content TEXT NOT NULL CHECK(length(content) BETWEEN 1 AND 8192),
    recorded_at TEXT NOT NULL,
    fact_references TEXT NOT NULL CHECK(json_valid(fact_references) AND json_type(fact_references) = 'array'),
    prompt_version TEXT,
    UNIQUE(conversation_id, ordinal),
    FOREIGN KEY(conversation_id,event_id) REFERENCES events(conversation_id,event_id)
) STRICT;
CREATE TABLE transitions (
    id INTEGER PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
    previous TEXT NOT NULL CHECK(previous IN ('AUTO','WAITING_HUMAN','HUMAN_ACTIVE','RESOLVED')),
    current TEXT NOT NULL CHECK(current IN ('AUTO','WAITING_HUMAN','HUMAN_ACTIVE','RESOLVED')),
    action TEXT NOT NULL,
    operator_id TEXT,
    recorded_at TEXT NOT NULL,
    UNIQUE(conversation_id,ordinal)
) STRICT;
CREATE TABLE audit (
    id INTEGER PRIMARY KEY,
    operation TEXT NOT NULL,
    entity TEXT NOT NULL,
    origin TEXT NOT NULL CHECK(origin IN ('client','operator','storage')),
    outcome TEXT NOT NULL CHECK(outcome IN ('committed','duplicate','rolled_back','retired')),
    recorded_at TEXT NOT NULL
) STRICT;
