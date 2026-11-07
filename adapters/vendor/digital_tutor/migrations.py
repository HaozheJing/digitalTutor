"""Additive business database migrations; never touch Hermes databases."""
from .domain import require


def upgrade(connection, version):
    require(version in (1, 2, 3), 'DATABASE_VERSION', '数据库版本不受支持')
    if version == 1:
        connection.executescript('''BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS runtime_control(
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), generation INTEGER NOT NULL);
            INSERT OR IGNORE INTO runtime_control VALUES(1,1,1);
            INSERT OR IGNORE INTO schema_migrations VALUES(2);
            PRAGMA user_version=2;
            COMMIT;''')
    if version < 3:
        connection.executescript('''BEGIN IMMEDIATE;
            CREATE INDEX IF NOT EXISTS job_status_lookup ON records(json_extract(data,'$.status'),family_id)
                WHERE kind='job';
            CREATE INDEX IF NOT EXISTS operation_job_lookup ON records(family_id,json_extract(data,'$.send_capability.job_id'))
                WHERE kind='operation';
            INSERT OR IGNORE INTO schema_migrations VALUES(3);
            PRAGMA user_version=3;
            COMMIT;''')
