from contextlib import contextmanager
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, Session
from shared.config import get_settings
from shared.models import Base

_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        connect_args = {}
        if settings.DATABASE_URL.startswith("sqlite"):
            connect_args = {"check_same_thread": False}
        _engine = create_engine(
            settings.DATABASE_URL,
            pool_pre_ping=True,
            connect_args=connect_args,
        )
    return _engine


def get_session_factory():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=get_engine())
    return _SessionLocal


def get_db():
    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Session:
    db = get_session_factory()()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _ensure_bot_config_columns(engine):
    """Idempotent schema migration for newly added strategy columns on existing DB volumes."""
    insp = inspect(engine)
    if "bot_configs" not in insp.get_table_names():
        return
    existing = {col["name"] for col in insp.get_columns("bot_configs")}
    is_pg = engine.dialect.name == "postgresql"
    bool_true = "TRUE" if is_pg else "1"
    migrations = [
        ("strategy_mode", "VARCHAR(32) NOT NULL DEFAULT 'SMART_CHANNEL_DCA'"),
        ("channel_window", "INTEGER NOT NULL DEFAULT 60"),
        ("channel_sigma", "DOUBLE PRECISION NOT NULL DEFAULT 2.0" if is_pg else "FLOAT NOT NULL DEFAULT 2.0"),
        ("atr_period", "INTEGER NOT NULL DEFAULT 10"),
        ("tp_atr_mult", "DOUBLE PRECISION NOT NULL DEFAULT 0.75" if is_pg else "FLOAT NOT NULL DEFAULT 0.75"),
        ("sl_atr_mult", "DOUBLE PRECISION NOT NULL DEFAULT 1.90" if is_pg else "FLOAT NOT NULL DEFAULT 1.90"),
        ("use_crash_guard", f"BOOLEAN NOT NULL DEFAULT {bool_true}"),
    ]
    with engine.begin() as conn:
        for col_name, col_ddl in migrations:
            if col_name not in existing:
                conn.execute(text(f"ALTER TABLE bot_configs ADD COLUMN {col_name} {col_ddl}"))


def init_db():
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    _ensure_bot_config_columns(engine)
