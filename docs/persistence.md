# Persistence

Runtime state is stored under the private platform data directory in `database/runtime.sqlite3`. SQLite uses WAL, foreign keys, a busy timeout, schema versioning and atomic transactions. Active latches, immutable decisions, source snapshots, alerts and acknowledgements are stored. Unsupported schema versions are backed up before migration. Corruption or write failure is fail-closed and prevents GREEN.
