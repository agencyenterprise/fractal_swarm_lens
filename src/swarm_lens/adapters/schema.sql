CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL, metadata TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS branches (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), name TEXT NOT NULL,
    parent_id TEXT REFERENCES branches(id), fork_position INTEGER NOT NULL CHECK(fork_position >= 0),
    head INTEGER NOT NULL CHECK(head >= fork_position), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY, branch_id TEXT NOT NULL REFERENCES branches(id), position INTEGER NOT NULL,
    kind TEXT NOT NULL, data TEXT NOT NULL, occurred_at TEXT NOT NULL, recorded_at TEXT NOT NULL,
    source TEXT NOT NULL, schema_version INTEGER NOT NULL, text_refs TEXT, UNIQUE(branch_id, position)
);
-- Each large text is stored once, zlib-compressed; `text_refs` names the event data fields holding its digest.
CREATE TABLE IF NOT EXISTS texts (digest TEXT PRIMARY KEY, body BLOB NOT NULL);
CREATE INDEX IF NOT EXISTS events_branch_position ON events(branch_id, position);
CREATE TABLE IF NOT EXISTS agents (
    run_id TEXT NOT NULL REFERENCES runs(id), id TEXT NOT NULL, PRIMARY KEY(run_id, id)
);
CREATE TABLE IF NOT EXISTS channels (
    run_id TEXT NOT NULL REFERENCES runs(id), id TEXT NOT NULL, PRIMARY KEY(run_id, id)
);
CREATE TABLE IF NOT EXISTS agent_revisions (
    event_id TEXT PRIMARY KEY REFERENCES events(id), run_id TEXT NOT NULL, agent_id TEXT NOT NULL,
    name TEXT NOT NULL, model TEXT, system_prompt TEXT, active INTEGER NOT NULL,
    FOREIGN KEY(run_id, agent_id) REFERENCES agents(run_id, id)
);
CREATE TABLE IF NOT EXISTS channel_revisions (
    event_id TEXT PRIMARY KEY REFERENCES events(id), run_id TEXT NOT NULL, channel_id TEXT NOT NULL,
    name TEXT NOT NULL, FOREIGN KEY(run_id, channel_id) REFERENCES channels(run_id, id)
);
CREATE TABLE IF NOT EXISTS channel_members (
    event_id TEXT NOT NULL REFERENCES channel_revisions(event_id), run_id TEXT NOT NULL,
    agent_id TEXT NOT NULL, PRIMARY KEY(event_id, agent_id),
    FOREIGN KEY(run_id, agent_id) REFERENCES agents(run_id, id)
);
CREATE TABLE IF NOT EXISTS messages (
    event_id TEXT PRIMARY KEY REFERENCES events(id), message_id TEXT NOT NULL,
    run_id TEXT NOT NULL, channel_id TEXT NOT NULL, sender_id TEXT, sender_name TEXT,
    role TEXT NOT NULL, content TEXT, reply_to_id TEXT, content_digest TEXT REFERENCES texts(digest),
    CHECK((content IS NULL) <> (content_digest IS NULL)),
    FOREIGN KEY(run_id, channel_id) REFERENCES channels(run_id, id),
    FOREIGN KEY(run_id, sender_id) REFERENCES agents(run_id, id)
);
CREATE TABLE IF NOT EXISTS memory_revisions (
    event_id TEXT PRIMARY KEY REFERENCES events(id), memory_id TEXT NOT NULL,
    run_id TEXT NOT NULL, owner_id TEXT, scope TEXT NOT NULL, content TEXT,
    content_digest TEXT REFERENCES texts(digest), CHECK((content IS NULL) <> (content_digest IS NULL)),
    FOREIGN KEY(run_id, owner_id) REFERENCES agents(run_id, id)
);
CREATE TABLE IF NOT EXISTS tool_revisions (
    event_id TEXT PRIMARY KEY REFERENCES events(id), tool_call_id TEXT NOT NULL,
    run_id TEXT NOT NULL, agent_id TEXT NOT NULL, tool_name TEXT NOT NULL,
    arguments TEXT NOT NULL, result TEXT, error TEXT, status TEXT NOT NULL,
    FOREIGN KEY(run_id, agent_id) REFERENCES agents(run_id, id)
);
CREATE TABLE IF NOT EXISTS environment_revisions (
    event_id TEXT PRIMARY KEY REFERENCES events(id), task TEXT NOT NULL, goal TEXT NOT NULL
);
-- A regenerable cache (see snapshots.py): format 1 rows hold a whole state as JSON text; format 2 rows hold
-- zlib-compressed JSON, either a full state (no base) or a delta on the snapshot at (base_branch, base_cursor).
CREATE TABLE IF NOT EXISTS snapshots (
    branch_id TEXT NOT NULL REFERENCES branches(id), cursor INTEGER NOT NULL,
    state BLOB NOT NULL, base_branch TEXT, base_cursor INTEGER, chain_entities INTEGER NOT NULL DEFAULT 0,
    full_entities INTEGER NOT NULL DEFAULT 0, format INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(branch_id, cursor)
);
CREATE TABLE IF NOT EXISTS analyses (
    id TEXT PRIMARY KEY, branch_id TEXT NOT NULL REFERENCES branches(id),
    cursor INTEGER NOT NULL, record TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS comments (
    id TEXT PRIMARY KEY, branch_id TEXT NOT NULL REFERENCES branches(id),
    event_id TEXT NOT NULL REFERENCES events(id), position INTEGER NOT NULL CHECK(position >= 1),
    author TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL,
    parent_id TEXT REFERENCES comments(id) ON DELETE CASCADE,
    resolved INTEGER NOT NULL CHECK(resolved IN (0, 1)), updated_at TEXT
);
CREATE INDEX IF NOT EXISTS comments_branch_position ON comments(branch_id, position);
CREATE INDEX IF NOT EXISTS comments_parent ON comments(parent_id);
