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


def _ensure_schema_columns(engine):
    """Idempotent schema migration for newly added columns on existing DB volumes."""
    insp = inspect(engine)
    is_pg = engine.dialect.name == "postgresql"
    bool_true = "TRUE" if is_pg else "1"

    # 1. bot_configs table migrations
    if "bot_configs" in insp.get_table_names():
        existing_bot = {col["name"] for col in insp.get_columns("bot_configs")}
        bot_migrations = [
            ("strategy_mode", "VARCHAR(32) NOT NULL DEFAULT 'SMART_CHANNEL_DCA'"),
            ("channel_window", "INTEGER NOT NULL DEFAULT 60"),
            ("channel_sigma", "DOUBLE PRECISION NOT NULL DEFAULT 2.0" if is_pg else "FLOAT NOT NULL DEFAULT 2.0"),
            ("atr_period", "INTEGER NOT NULL DEFAULT 10"),
            ("tp_atr_mult", "DOUBLE PRECISION NOT NULL DEFAULT 0.75" if is_pg else "FLOAT NOT NULL DEFAULT 0.75"),
            ("sl_atr_mult", "DOUBLE PRECISION NOT NULL DEFAULT 1.90" if is_pg else "FLOAT NOT NULL DEFAULT 1.90"),
            ("use_crash_guard", f"BOOLEAN NOT NULL DEFAULT {bool_true}"),
            ("deep_analysis_enabled", f"BOOLEAN NOT NULL DEFAULT {bool_true}"),
            ("conservative_mode_enabled", f"BOOLEAN NOT NULL DEFAULT {bool_true}"),
            ("tick_duration", "INTEGER NOT NULL DEFAULT 1"),
            ("martingale_factor", "DOUBLE PRECISION NOT NULL DEFAULT 2.15" if is_pg else "FLOAT NOT NULL DEFAULT 2.15"),
            ("active_indices_json", "TEXT NOT NULL DEFAULT '[\"cryETHUSD\", \"cryBTCUSD\", \"JD75\", \"R_100\", \"1HZ100V\", \"R_50\", \"JD50\"]'"),
            ("sniper_enabled", f"BOOLEAN NOT NULL DEFAULT {'FALSE' if is_pg else '0'}"),
            ("sniper_config_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("sniper_state_json", "TEXT NOT NULL DEFAULT '{}'"),
        ]
        with engine.begin() as conn:
            for col_name, col_ddl in bot_migrations:
                if col_name not in existing_bot:
                    conn.execute(text(f"ALTER TABLE bot_configs ADD COLUMN {col_name} {col_ddl}"))

    # 2. users table migrations
    if "users" in insp.get_table_names():
        existing_users = {col["name"] for col in insp.get_columns("users")}
        user_migrations = [
            ("can_access_robo_deriv", f"BOOLEAN NOT NULL DEFAULT {bool_true}"),
            ("allowed_modules_json", "TEXT NOT NULL DEFAULT '[\"radar_btc\", \"robo_deriv\"]'"),
            ("current_session_id", "VARCHAR(64) NULL"),
            ("current_session_ip", "VARCHAR(64) NULL"),
        ]
        with engine.begin() as conn:
            for col_name, col_ddl in user_migrations:
                if col_name not in existing_users:
                    conn.execute(text(f"ALTER TABLE users ADD COLUMN {col_name} {col_ddl}"))


def init_db():
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    _ensure_schema_columns(engine)
