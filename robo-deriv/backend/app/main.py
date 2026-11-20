import logging
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api import admin, auth, bot, btc_radar, deriv, market, reports, ws
from app.core.redis_client import ping_redis
from app.core.security import hash_password
from app.radar.service import RadarService
from app.services.subscription import ensure_bot_config
from shared.config import get_settings
from shared.database import get_session_factory, init_db
from shared.models import User, utcnow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def seed_default_admin() -> None:
    """Seed the default ADMIN user and default BotConfig if not already present."""
    settings = get_settings()
    admin_email = settings.ADMIN_EMAIL.strip().lower()
    db = get_session_factory()()
    try:
        existing = db.query(User).filter(User.email == admin_email).first()
        if existing is None:
            admin_user = User(
                name=settings.ADMIN_NAME.strip() or "Administrador",
                email=admin_email,
                password_hash=hash_password(settings.ADMIN_PASSWORD),
                role="ADMIN",
                status="ACTIVE",
                expires_at=utcnow() + timedelta(days=3650),
                max_stake_usd=500.0,
                must_change_password=True,
            )
            db.add(admin_user)
            db.flush()
            ensure_bot_config(db, admin_user.id)
            db.commit()
            logger.info("Seeded default admin user: %s", admin_email)
        else:
            ensure_bot_config(db, existing.id)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    seed_default_admin()
    radar = RadarService.get_instance()
    await radar.start()
    yield
    radar.stop()


def create_app() -> FastAPI:
    settings = get_settings()
    origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
    if not origins:
        origins = ["http://localhost:8095", "http://127.0.0.1:8095"]

    application = FastAPI(
        title="Deriv Smart DCA SaaS API",
        version="1.0.0",
        description="Multi-user quantitative Deriv Multipliers Channel + Smart DCA platform API",
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    application.include_router(auth.router)
    application.include_router(admin.router)
    application.include_router(deriv.router)
    application.include_router(market.router)
    application.include_router(bot.router)
    application.include_router(reports.router)
    application.include_router(btc_radar.router)
    application.include_router(btc_radar.ws_router)
    application.include_router(ws.router)

    @application.get("/api/health", tags=["Health"])
    def health_check():
        db_status = "ok"
        db = get_session_factory()()
        try:
            db.execute(text("SELECT 1"))
        except Exception:
            db_status = "error"
        finally:
            db.close()

        redis_status = "ok" if ping_redis() else "unavailable"
        return {
            "status": "ok" if db_status == "ok" else "degraded",
            "service": "deriv-dca-backend",
            "database": db_status,
            "redis": redis_status,
        }

    return application


app = create_app()
