from collections.abc import Iterator

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

from cvm.config import settings

settings.data_dir.mkdir(parents=True, exist_ok=True)
engine = create_engine(f"sqlite:///{settings.db_path}", connect_args={"check_same_thread": False, "timeout": 30})


def _add_missing_columns() -> None:
    """Tiny forward-only migration: add columns that exist in the models but not in SQLite yet."""
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in existing:
                    ddl_type = col.type.compile(engine.dialect)
                    default = " DEFAULT '{}'" if ddl_type == "JSON" else ""
                    conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl_type}{default}'))


def init_db() -> None:
    from cvm import models  # noqa: F401  (register tables)

    SQLModel.metadata.create_all(engine)
    _add_missing_columns()
    with engine.begin() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
