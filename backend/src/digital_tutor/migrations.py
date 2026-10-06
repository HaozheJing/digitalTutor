"""Additive business database migrations; never touch Hermes databases."""
from .domain import require


def upgrade(connection, version):
    require(version in (1, 2), 'DATABASE_VERSION', '数据库版本不受支持')
    if version == 1:
        connection.executescript('''BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS runtime_control(
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), generation INTEGER NOT NULL);
            INSERT OR IGNORE INTO runtime_control VALUES(1,1,1);
            INSERT OR IGNORE INTO schema_migrations VALUES(2);
            PRAGMA user_version=2;
            COMMIT;''')
