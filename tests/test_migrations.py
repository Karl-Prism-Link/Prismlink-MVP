from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from database.models import Base


def test_initial_migration_creates_current_schema(tmp_path):
    database_path = tmp_path / "migrated.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")

    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        expected_tables = set(Base.metadata.tables)
        assert tables - {"alembic_version"} == expected_tables
        for table_name, table in Base.metadata.tables.items():
            assert {column["name"] for column in inspector.get_columns(table_name)} == {
                column.name for column in table.columns
            }
    finally:
        engine.dispose()

    assert "alembic_version" in tables
