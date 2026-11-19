import csv
import io
import json
import os
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Set test environment variables before importing app/shared modules
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["REDIS_URL"] = "redis://127.0.0.1:6399/0"  # Non-listening port to force graceful fallback
os.environ["JWT_SECRET"] = "test-super-secret-jwt-key-32-chars-minimum!!"
os.environ["ENCRYPTION_KEY"] = ""
os.environ["ADMIN_EMAIL"] = "admin@test.local"
os.environ["ADMIN_PASSWORD"] = "Admin@123456"
os.environ["DERIV_APP_ID"] = "1089"

from shared import config as shared_config
from shared import database as shared_db
from shared.crypto_vault import decrypt_secret
from shared.models import Base, Basket, BotConfig, Contract, DerivAccount, User, utcnow

from app.core.redis_client import reset_sync_redis
from app.core.security import login_rate_limiter
from app.main import create_app
from app.services.deriv_service import DerivScopeError, compute_causal_channel


@pytest.fixture()
def test_env():
    """Spin up an isolated in-memory SQLite database and FastAPI TestClient per test."""
    shared_config.get_settings.cache_clear()
    reset_sync_redis()
    login_rate_limiter.clear_memory()

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    shared_db._engine = engine
    shared_db._SessionLocal = TestingSessionLocal
    Base.metadata.create_all(bind=engine)

    app = create_app()
    with TestClient(app) as client:
        yield client, TestingSessionLocal

    Base.metadata.drop_all(bind=engine)
    engine.dispose()
    shared_db._engine = None
    shared_db._SessionLocal = None


def _login(client: TestClient, email: str, password: str) -> dict:
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# 1. Admin Seed, Auth, Refresh, Change Password & Health
# ---------------------------------------------------------------------------
def test_admin_seed_login_and_auth_flow(test_env):
    client, _ = test_env

    # Health check
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["database"] == "ok"

    # Admin seeded automatically on startup
    login_data = _login(client, "admin@test.local", "Admin@123456")
    assert login_data["token_type"] == "bearer"
    assert login_data["user"]["role"] == "ADMIN"
    assert login_data["user"]["is_expired"] is False
    assert login_data["user"]["days_remaining"] >= 3649
    assert "password_hash" not in login_data["user"]

    token = login_data["access_token"]

    # /api/auth/me
    me_resp = client.get("/api/auth/me", headers=_auth_headers(token))
    assert me_resp.status_code == 200
    assert me_resp.json()["email"] == "admin@test.local"

    # Refresh token via HttpOnly cookie set on login
    refresh_resp = client.post("/api/auth/refresh")
    assert refresh_resp.status_code == 200
    assert refresh_resp.json()["access_token"]

    # Change password
    cp_resp = client.post(
        "/api/auth/change-password",
        headers=_auth_headers(token),
        json={"current_password": "Admin@123456", "new_password": "NewAdmin@987654"},
    )
    assert cp_resp.status_code == 200
    assert cp_resp.json()["user"]["must_change_password"] is False

    # Old password fails, new password works
    bad = client.post(
        "/api/auth/login",
        json={"email": "admin@test.local", "password": "Admin@123456"},
    )
    assert bad.status_code == 401

    good = _login(client, "admin@test.local", "NewAdmin@987654")
    assert good["user"]["email"] == "admin@test.local"

    # Logout clears cookie
    out = client.post("/api/auth/logout")
    assert out.status_code == 200


# ---------------------------------------------------------------------------
# 2. RBAC & Admin CRUD + Extend Expiration
# ---------------------------------------------------------------------------
def test_rbac_and_admin_user_lifecycle(test_env):
    client, _ = test_env
    admin_login = _login(client, "admin@test.local", "Admin@123456")
    admin_headers = _auth_headers(admin_login["access_token"])
    admin_id = admin_login["user"]["id"]

    # Unauthenticated blocked
    assert client.get("/api/admin/users").status_code == 401

    # Admin creates regular user with 10 valid_days
    create_resp = client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={
            "name": "Trader Alice",
            "email": "alice@test.local",
            "password": "UserPass@123",
            "valid_days": 10,
            "max_stake_usd": 25.0,
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    alice = create_resp.json()
    alice_id = alice["id"]
    assert alice["role"] == "USER"
    assert alice["days_remaining"] in (10, 11)
    assert alice["is_expired"] is False
    assert alice["deriv_connected"] is False
    assert alice["bot_enabled"] is False

    # Regular user CANNOT access /api/admin/*
    alice_login = _login(client, "alice@test.local", "UserPass@123")
    alice_headers = _auth_headers(alice_login["access_token"])

    assert client.get("/api/admin/users", headers=alice_headers).status_code == 403
    assert client.get("/api/admin/health", headers=alice_headers).status_code == 403
    assert (
        client.post(f"/api/admin/users/{alice_id}/extend", headers=alice_headers, json={"days": 30}).status_code
        == 403
    )

    # Admin extends user by 30 days
    ext_resp = client.post(
        f"/api/admin/users/{alice_id}/extend",
        headers=admin_headers,
        json={"days": 30},
    )
    assert ext_resp.status_code == 200
    assert ext_resp.json()["days_remaining"] >= 39

    # Admin updates user
    upd_resp = client.put(
        f"/api/admin/users/{alice_id}",
        headers=admin_headers,
        json={"name": "Alice Pro", "max_stake_usd": 100.0},
    )
    assert upd_resp.status_code == 200
    assert upd_resp.json()["name"] == "Alice Pro"
    assert upd_resp.json()["max_stake_usd"] == 100.0

    # Admin cannot delete self
    del_self = client.delete(f"/api/admin/users/{admin_id}", headers=admin_headers)
    assert del_self.status_code == 400

    # Admin health endpoint works
    health_resp = client.get("/api/admin/health", headers=admin_headers)
    assert health_resp.status_code == 200
    assert health_resp.json()["postgres"]["ok"] is True
    assert health_resp.json()["stats"]["total_users"] == 2

    # Admin deletes Alice
    del_resp = client.delete(f"/api/admin/users/{alice_id}", headers=admin_headers)
    assert del_resp.status_code == 200


# ---------------------------------------------------------------------------
# 3. Expired User Can Login But Cannot Enable Bot; Suspended Cannot Login
# ---------------------------------------------------------------------------
def test_expired_user_blocked_from_enabling_bot(test_env):
    client, SessionLocal = test_env
    admin_headers = _auth_headers(_login(client, "admin@test.local", "Admin@123456")["access_token"])

    # Create user already expired 2 days ago
    past_dt = (utcnow() - timedelta(days=2)).isoformat()
    create_resp = client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={
            "name": "Expired Bob",
            "email": "bob@test.local",
            "password": "BobPass@123",
            "expires_at": past_dt,
        },
    )
    assert create_resp.status_code == 201
    bob_id = create_resp.json()["id"]
    assert create_resp.json()["is_expired"] is True
    assert create_resp.json()["days_remaining"] == 0

    # Bob CAN still log in to view his expired banner & reports
    bob_login = _login(client, "bob@test.local", "BobPass@123")
    assert bob_login["user"]["is_expired"] is True
    bob_headers = _auth_headers(bob_login["access_token"])

    # Bob CAN view reports
    rep = client.get("/api/reports/summary?account_type=DEMO", headers=bob_headers)
    assert rep.status_code == 200

    # Connect a Deriv account for Bob first
    with patch(
        "app.api.deriv.verify_deriv_token",
        new=AsyncMock(
            return_value={
                "loginid": "VRTC999001",
                "currency": "USD",
                "balance": 10000.0,
                "is_virtual": True,
                "account_type": "DEMO",
                "scopes": ["read", "trade"],
            }
        ),
    ):
        conn = client.post(
            "/api/deriv/connect-token",
            headers=bob_headers,
            json={"token": "secret_demo_token_bob_12345"},
        )
        assert conn.status_code == 200

    # Bob CANNOT enable the bot while expired!
    toggle_resp = client.post("/api/bot/toggle", headers=bob_headers, json={"enabled": True})
    assert toggle_resp.status_code == 403
    assert "expirada" in toggle_resp.json()["detail"].lower()

    # Admin extends Bob from max(now, expires_at) + 15 days -> should now have ~15 days!
    ext = client.post(f"/api/admin/users/{bob_id}/extend", headers=admin_headers, json={"days": 15})
    assert ext.status_code == 200
    assert ext.json()["is_expired"] is False
    assert ext.json()["days_remaining"] >= 14

    # Now Bob CAN enable the bot!
    toggle_ok = client.post("/api/bot/toggle", headers=bob_headers, json={"enabled": True})
    assert toggle_ok.status_code == 200
    assert toggle_ok.json()["enabled"] is True

    # If Admin suspends Bob, Bob can no longer login
    susp = client.put(f"/api/admin/users/{bob_id}", headers=admin_headers, json={"status": "SUSPENDED"})
    assert susp.status_code == 200
    login_susp = client.post("/api/auth/login", json={"email": "bob@test.local", "password": "BobPass@123"})
    assert login_susp.status_code == 403


# ---------------------------------------------------------------------------
# 4. Deriv Token Encryption, Zero Leaks, Scope Rejection & REAL Risk Guard
# ---------------------------------------------------------------------------
def test_deriv_token_encryption_no_leak_and_real_risk_guard(test_env):
    client, SessionLocal = test_env
    admin_login = _login(client, "admin@test.local", "Admin@123456")
    headers = _auth_headers(admin_login["access_token"])

    # OAuth URL endpoint
    oauth_url = client.get("/api/deriv/oauth-url", headers=headers)
    assert oauth_url.status_code == 200
    assert "https://oauth.deriv.com/oauth2/authorize?app_id=1089" == oauth_url.json()["oauth_url"]

    # Reject forbidden scope token (payments/admin)
    with patch(
        "app.api.deriv.verify_deriv_token",
        new=AsyncMock(side_effect=DerivScopeError("Token rejeitado: permissão proibida (payments)")),
    ):
        bad_scope = client.post(
            "/api/deriv/connect-token",
            headers=headers,
            json={"token": "forbidden_payments_token_999"},
        )
        assert bad_scope.status_code == 400
        assert "payments" in bad_scope.json()["detail"].lower()

    # Connect OAuth with both REAL and DEMO accounts -> DEMO selected by default!
    raw_real_token = "REAL_SUPER_SECRET_TOKEN_XYZ123"
    raw_demo_token = "DEMO_SUPER_SECRET_TOKEN_ABC456"

    async def _fake_verify(tok: str):
        if tok == raw_real_token:
            return {
                "loginid": "CR555111",
                "currency": "USD",
                "balance": 250.50,
                "is_virtual": False,
                "account_type": "REAL",
                "scopes": ["read", "trade"],
            }
        return {
            "loginid": "VRTC555222",
            "currency": "USD",
            "balance": 10000.0,
            "is_virtual": True,
            "account_type": "DEMO",
            "scopes": ["read", "trade"],
        }

    with patch("app.api.deriv.verify_deriv_token", side_effect=_fake_verify):
        oauth_resp = client.post(
            "/api/deriv/connect-oauth",
            headers=headers,
            json={
                "accounts": [
                    {"account": "CR555111", "token": raw_real_token, "currency": "USD"},
                    {"account": "VRTC555222", "token": raw_demo_token, "currency": "USD"},
                ]
            },
        )
    assert oauth_resp.status_code == 200, oauth_resp.text
    body_text = oauth_resp.text
    # Ensure ZERO tokens leaked in API response
    assert raw_real_token not in body_text
    assert raw_demo_token not in body_text
    assert "token_encrypted" not in body_text

    accounts = oauth_resp.json()["accounts"]
    assert len(accounts) == 2
    demo_acc = next(a for a in accounts if a["account_type"] == "DEMO")
    real_acc = next(a for a in accounts if a["account_type"] == "REAL")
    assert demo_acc["is_active_selected"] is True
    assert real_acc["is_active_selected"] is False

    # Verify DB stores encrypted tokens (NOT plaintext) and decrypts cleanly
    db = SessionLocal()
    try:
        db_real = db.query(DerivAccount).filter(DerivAccount.loginid == "CR555111").first()
        assert db_real is not None
        assert db_real.token_encrypted != raw_real_token
        assert decrypt_secret(db_real.token_encrypted) == raw_real_token
    finally:
        db.close()

    # Ensure ZERO tokens leaked in /api/deriv/accounts, /api/bot/config, /api/admin/users
    for endpoint in ("/api/deriv/accounts", "/api/bot/config", "/api/admin/users"):
        r = client.get(endpoint, headers=headers)
        assert r.status_code == 200
        assert raw_real_token not in r.text
        assert raw_demo_token not in r.text
        assert "token_encrypted" not in r.text

    # Switching to REAL without confirm_real_risk=True MUST FAIL
    fail_real = client.post(
        "/api/deriv/select-account",
        headers=headers,
        json={"account_id": real_acc["id"], "confirm_real_risk": False},
    )
    assert fail_real.status_code == 400

    # Create an OPEN basket on the DEMO account -> switching accounts MUST be refused (409)
    db = SessionLocal()
    try:
        open_b = Basket(
            user_id=admin_login["user"]["id"],
            deriv_account_id=demo_acc["id"],
            account_type="DEMO",
            symbol="cryBTCUSD",
            direction="MULTUP",
            status="OPEN",
            layers_count=1,
            total_stake_usd=1.0,
        )
        db.add(open_b)
        db.commit()
        open_b_id = open_b.id
    finally:
        db.close()

    blocked_switch = client.post(
        "/api/deriv/select-account",
        headers=headers,
        json={"account_id": real_acc["id"], "confirm_real_risk": True},
    )
    assert blocked_switch.status_code == 409

    # Deleting DEMO account with open basket MUST also fail (409)
    blocked_del = client.delete(f"/api/deriv/accounts/{demo_acc['id']}", headers=headers)
    assert blocked_del.status_code == 409

    # Close the basket -> now switching to REAL with confirm_real_risk=True succeeds!
    db = SessionLocal()
    try:
        b = db.query(Basket).filter(Basket.id == open_b_id).first()
        b.status = "CLOSED"
        b.closed_at = utcnow()
        db.commit()
    finally:
        db.close()

    ok_real = client.post(
        "/api/deriv/select-account",
        headers=headers,
        json={"account_id": real_acc["id"], "confirm_real_risk": True},
    )
    assert ok_real.status_code == 200
    assert ok_real.json()["selected_account"]["account_type"] == "REAL"

    # Sync balance decrypts stored token and updates DB
    with patch(
        "app.api.deriv.fetch_account_balance",
        new=AsyncMock(
            return_value={
                "loginid": "CR555111",
                "currency": "USD",
                "balance": 312.75,
                "is_virtual": False,
                "account_type": "REAL",
            }
        ),
    ):
        sync = client.post(
            "/api/deriv/sync-balance",
            headers=headers,
            json={"account_id": real_acc["id"]},
        )
        assert sync.status_code == 200
        updated_real = next(a for a in sync.json()["accounts"] if a["id"] == real_acc["id"])
        assert updated_real["balance"] == 312.75


# ---------------------------------------------------------------------------
# 5. BotConfig Validation, Toggle & Kill Switch
# ---------------------------------------------------------------------------
def test_bot_config_validation_and_kill_switch(test_env):
    client, _ = test_env
    admin_headers = _auth_headers(_login(client, "admin@test.local", "Admin@123456")["access_token"])

    # Create a user with max_stake_usd=20.0
    u_resp = client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={
            "name": "Trader Carla",
            "email": "carla@test.local",
            "password": "CarlaPass@123",
            "valid_days": 30,
            "max_stake_usd": 20.0,
        },
    )
    assert u_resp.status_code == 201
    carla_headers = _auth_headers(_login(client, "carla@test.local", "CarlaPass@123")["access_token"])

    # Validation errors
    assert client.put("/api/bot/config", headers=carla_headers, json={"stake_usd": 0.5}).status_code == 400
    assert client.put("/api/bot/config", headers=carla_headers, json={"stake_usd": 25.0}).status_code == 400
    assert client.put("/api/bot/config", headers=carla_headers, json={"max_layers": 0}).status_code == 400
    assert client.put("/api/bot/config", headers=carla_headers, json={"max_layers": 6}).status_code == 400
    assert client.put("/api/bot/config", headers=carla_headers, json={"basket_tp_usd": -0.1}).status_code == 400
    assert client.put("/api/bot/config", headers=carla_headers, json={"max_basket_loss_usd": 0}).status_code == 400
    # Martingale 2.0x+ rejected
    assert (
        client.put("/api/bot/config", headers=carla_headers, json={"layer_multipliers": [1.0, 2.0, 4.0]}).status_code
        == 400
    )

    # Valid config update publishes RELOAD_CONFIG
    with patch("app.api.bot.publish_control_event") as mock_pub:
        ok_cfg = client.put(
            "/api/bot/config",
            headers=carla_headers,
            json={
                "symbol": "frxEURUSD",
                "timeframe_sec": 900,
                "direction_mode": "BOTH",
                "stake_usd": 5.0,
                "multiplier": 100,
                "max_layers": 3,
                "layer_multipliers": [1.0, 1.2, 1.5],
                "basket_tp_usd": 0.40,
                "max_basket_loss_usd": 5.0,
            },
        )
        assert ok_cfg.status_code == 200
        assert ok_cfg.json()["config"]["symbol"] == "frxEURUSD"
        assert ok_cfg.json()["config"]["stake_usd"] == 5.0
        mock_pub.assert_called_once()
        assert mock_pub.call_args[0][0]["action"] == "RELOAD_CONFIG"

    # Toggle ON without Deriv account MUST fail (400)
    no_acc = client.post("/api/bot/toggle", headers=carla_headers, json={"enabled": True})
    assert no_acc.status_code == 400

    # Connect Deriv account and toggle ON
    with patch(
        "app.api.deriv.verify_deriv_token",
        new=AsyncMock(
            return_value={
                "loginid": "VRTC777888",
                "currency": "USD",
                "balance": 10000.0,
                "is_virtual": True,
                "account_type": "DEMO",
                "scopes": ["read", "trade"],
            }
        ),
    ):
        assert (
            client.post(
                "/api/deriv/connect-token",
                headers=carla_headers,
                json={"token": "carla_demo_token_123456"},
            ).status_code
            == 200
        )

    with patch("app.api.bot.publish_control_event") as mock_pub:
        on_resp = client.post("/api/bot/toggle", headers=carla_headers, json={"enabled": True})
        assert on_resp.status_code == 200
        assert on_resp.json()["enabled"] is True
        assert mock_pub.call_args[0][0]["action"] == "START"

    # Kill Switch sets enabled=False, status_text="KILL_SWITCH", logs event, publishes KILL_SWITCH
    with patch("app.api.bot.publish_control_event") as mock_pub:
        ks = client.post("/api/bot/kill-switch", headers=carla_headers)
        assert ks.status_code == 200
        assert ks.json()["enabled"] is False
        assert ks.json()["status"] == "KILL_SWITCH"
        assert mock_pub.call_args[0][0]["action"] == "KILL_SWITCH"

    # Verify BotConfig persisted and event logged
    cfg_after = client.get("/api/bot/config", headers=carla_headers).json()
    assert cfg_after["config"]["enabled"] is False
    assert cfg_after["config"]["status_text"] == "KILL_SWITCH"

    events = client.get("/api/bot/events?limit=10", headers=carla_headers).json()["events"]
    assert any("KILL SWITCH" in ev["message"].upper() for ev in events)


# ---------------------------------------------------------------------------
# 6. Reports Strict DEMO vs REAL Separation & CSV Export
# ---------------------------------------------------------------------------
def test_reports_strict_demo_real_separation_and_csv(test_env):
    client, SessionLocal = test_env
    admin_login = _login(client, "admin@test.local", "Admin@123456")
    headers = _auth_headers(admin_login["access_token"])
    user_id = admin_login["user"]["id"]

    now = utcnow()
    db = SessionLocal()
    try:
        # 3 DEMO closed cycles: +0.20, -0.10, +0.30 -> total +0.40, 2 wins, 1 loss, PF = 5.0
        demo_baskets = [
            Basket(
                user_id=user_id,
                account_type="DEMO",
                symbol="cryBTCUSD",
                direction="MULTUP",
                status="CLOSED",
                layers_count=1,
                total_stake_usd=1.0,
                total_commission_usd=0.07,
                avg_entry_price=65000.0,
                realized_pnl_usd=0.20,
                close_reason="TP",
                opened_at=now - timedelta(hours=5),
                closed_at=now - timedelta(hours=4, minutes=50),
            ),
            Basket(
                user_id=user_id,
                account_type="DEMO",
                symbol="cryBTCUSD",
                direction="MULTUP",
                status="CLOSED",
                layers_count=3,
                total_stake_usd=3.5,
                total_commission_usd=0.24,
                avg_entry_price=64800.0,
                realized_pnl_usd=-0.10,
                close_reason="STOP_LOSS",
                opened_at=now - timedelta(hours=4),
                closed_at=now - timedelta(hours=3, minutes=40),
            ),
            Basket(
                user_id=user_id,
                account_type="DEMO",
                symbol="frxEURUSD",
                direction="MULTDOWN",
                status="CLOSED",
                layers_count=2,
                total_stake_usd=2.0,
                total_commission_usd=0.04,
                avg_entry_price=1.0850,
                realized_pnl_usd=0.30,
                close_reason="TRAILING",
                opened_at=now - timedelta(hours=2),
                closed_at=now - timedelta(hours=1, minutes=50),
            ),
        ]
        # 1 REAL closed cycle: +5.00 -> MUST NEVER mix with DEMO!
        real_basket = Basket(
            user_id=user_id,
            account_type="REAL",
            symbol="cryBTCUSD",
            direction="MULTUP",
            status="CLOSED",
            layers_count=1,
            total_stake_usd=10.0,
            total_commission_usd=0.70,
            avg_entry_price=65200.0,
            realized_pnl_usd=5.00,
            close_reason="TP",
            opened_at=now - timedelta(hours=1),
            closed_at=now - timedelta(minutes=45),
        )
        db.add_all(demo_baskets + [real_basket])
        db.commit()
    finally:
        db.close()

    # DEMO summary
    demo_sum = client.get("/api/reports/summary?account_type=DEMO&days=30", headers=headers)
    assert demo_sum.status_code == 200
    d = demo_sum.json()
    assert d["account_type"] == "DEMO"
    assert d["total_cycles"] == 3
    assert d["winning_cycles"] == 2
    assert d["losing_cycles"] == 1
    assert d["total_pnl_usd"] == pytest.approx(0.40, abs=1e-4)
    assert d["profit_factor"] == pytest.approx(5.0, abs=1e-3)
    assert d["max_drawdown_usd"] == pytest.approx(0.10, abs=1e-4)
    assert len(d["equity_curve"]) == 3

    # REAL summary (completely isolated from DEMO!)
    real_sum = client.get("/api/reports/summary?account_type=REAL&days=30", headers=headers)
    assert real_sum.status_code == 200
    r = real_sum.json()
    assert r["account_type"] == "REAL"
    assert r["total_cycles"] == 1
    assert r["winning_cycles"] == 1
    assert r["total_pnl_usd"] == pytest.approx(5.0, abs=1e-4)

    # Symbol filter on DEMO
    eur_sum = client.get("/api/reports/summary?account_type=DEMO&days=30&symbol=frxEURUSD", headers=headers).json()
    assert eur_sum["total_cycles"] == 1
    assert eur_sum["total_pnl_usd"] == pytest.approx(0.30, abs=1e-4)

    # Paginated cycles
    cycles_resp = client.get("/api/reports/cycles?account_type=DEMO&page=1&page_size=2", headers=headers)
    assert cycles_resp.status_code == 200
    cy = cycles_resp.json()
    assert cy["total"] == 3
    assert cy["total_pages"] == 2
    assert len(cy["items"]) == 2
    assert all(item["account_type"] == "DEMO" for item in cy["items"])

    # CSV export for REAL contains ONLY REAL rows
    csv_resp = client.get("/api/reports/export.csv?account_type=REAL&days=30", headers=headers)
    assert csv_resp.status_code == 200
    assert "text/csv" in csv_resp.headers["content-type"]
    rows = list(csv.DictReader(io.StringIO(csv_resp.text)))
    assert len(rows) == 1
    assert rows[0]["account_type"] == "REAL"
    assert float(rows[0]["realized_pnl_usd"]) == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# 7. Market Endpoints, Causal Channel & Dashboard WebSocket
# ---------------------------------------------------------------------------
def test_market_endpoints_causal_channel_and_websocket(test_env):
    client, _ = test_env
    admin_login = _login(client, "admin@test.local", "Admin@123456")
    token = admin_login["access_token"]
    headers = _auth_headers(token)

    # Causal channel on synthetic uptrend candles
    synthetic_candles = [
        {
            "time": 1700000000 + i * 300,
            "open": 100.0 + i * 0.5,
            "high": 101.0 + i * 0.5,
            "low": 99.5 + i * 0.5,
            "close": 100.2 + i * 0.5 + (0.2 if i % 2 == 0 else -0.1),
        }
        for i in range(80)
    ]
    ch = compute_causal_channel(synthetic_candles, window=80)
    assert ch is not None
    assert ch["trend"] == "UP"
    assert ch["r2"] > 0.95
    assert ch["upper"] > ch["mid"] > ch["lower"]
    assert len(ch["points"]) == 80

    # Market endpoints with mocked Deriv service
    with patch(
        "app.api.market.fetch_multiplier_symbols",
        new=AsyncMock(
            return_value=[
                {
                    "symbol": "cryBTCUSD",
                    "display_name": "BTC/USD",
                    "market": "cryptocurrency",
                    "category": "Cripto",
                    "exchange_is_open": True,
                    "min_stake": 1.0,
                    "multipliers": [100, 200, 300],
                    "default_multiplier": 100,
                    "contract_types": ["MULTUP", "MULTDOWN"],
                }
            ]
        ),
    ):
        sym_resp = client.get("/api/market/symbols?search=btc", headers=headers)
        assert sym_resp.status_code == 200
        assert sym_resp.json()["count"] == 1
        assert sym_resp.json()["symbols"][0]["symbol"] == "cryBTCUSD"

    with patch(
        "app.api.market.fetch_proposal_preview",
        new=AsyncMock(
            return_value={
                "symbol": "cryBTCUSD",
                "amount": 1.0,
                "multiplier": 100,
                "contract_type": "MULTUP",
                "currency": "USD",
                "ask_price": 1.0,
                "commission": 0.07,
                "stop_out_value": 64395.5,
                "spot": 65000.0,
                "stop_out_pct": 0.93,
                "error": None,
            }
        ),
    ):
        prop_resp = client.get(
            "/api/market/proposal-preview?symbol=cryBTCUSD&amount=1.0&multiplier=100&contract_type=MULTUP",
            headers=headers,
        )
        assert prop_resp.status_code == 200
        assert prop_resp.json()["commission"] == 0.07
        assert prop_resp.json()["stop_out_pct"] == 0.93

    with patch(
        "app.api.market.fetch_candles",
        new=AsyncMock(
            return_value={
                "symbol": "cryBTCUSD",
                "granularity": 300,
                "candles": synthetic_candles,
                "channel": ch,
                "source": "live_deriv",
            }
        ),
    ):
        chart_resp = client.get("/api/market/chart?symbol=cryBTCUSD&granularity=300", headers=headers)
        assert chart_resp.status_code == 200
        assert len(chart_resp.json()["candles"]) == 80
        assert chart_resp.json()["channel"]["trend"] == "UP"

    # WebSocket rejects invalid token
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/dashboard?token=invalid_jwt"):
            pass

    # WebSocket accepts valid JWT, sends initial snapshot, handles ping -> pong
    with client.websocket_connect(f"/ws/dashboard?token={token}") as ws:
        snap = ws.receive_json()
        assert snap["type"] == "snapshot"
        assert snap["data"]["user"]["email"] == "admin@test.local"
        assert "config" in snap["data"]

        ws.send_text(json.dumps({"type": "ping"}))
        pong = ws.receive_json()
        assert pong == {"type": "pong"}


# ---------------------------------------------------------------------------
# 8. Login Brute-Force Rate Limiter & Deriv Service Unit Tests
# ---------------------------------------------------------------------------
def test_login_rate_limiter_blocks_brute_force(test_env):
    client, _ = test_env

    for _ in range(10):
        resp = client.post(
            "/api/auth/login",
            json={"email": "admin@test.local", "password": "WrongPassword!"},
        )
        assert resp.status_code == 401

    blocked = client.post(
        "/api/auth/login",
        json={"email": "admin@test.local", "password": "Admin@123456"},
    )
    assert blocked.status_code == 429


def test_deriv_service_verify_token_scopes_and_account_type():
    import asyncio
    from app.services.deriv_service import verify_deriv_token

    # Rejects payments scope
    with patch(
        "app.services.deriv_service._ws_single_request",
        new=AsyncMock(
            return_value={
                "authorize": {
                    "loginid": "CR100100",
                    "currency": "USD",
                    "balance": 500.0,
                    "is_virtual": 0,
                    "scopes": ["read", "trade", "payments"],
                }
            }
        ),
    ):
        with pytest.raises(DerivScopeError):
            asyncio.run(verify_deriv_token("tok_with_payments_123456"))

    # Rejects admin scope
    with patch(
        "app.services.deriv_service._ws_single_request",
        new=AsyncMock(
            return_value={
                "authorize": {
                    "loginid": "VRTC100100",
                    "currency": "USD",
                    "balance": 10000.0,
                    "is_virtual": 1,
                    "scopes": ["read", "trade", "admin"],
                }
            }
        ),
    ):
        with pytest.raises(DerivScopeError):
            asyncio.run(verify_deriv_token("tok_with_admin_123456"))

    # Correctly maps VRTC / is_virtual -> DEMO and CR -> REAL
    with patch(
        "app.services.deriv_service._ws_single_request",
        new=AsyncMock(
            return_value={
                "authorize": {
                    "loginid": "VRTC888111",
                    "currency": "USD",
                    "balance": 10000.0,
                    "is_virtual": 1,
                    "scopes": ["read", "trade"],
                }
            }
        ),
    ):
        res_demo = asyncio.run(verify_deriv_token("tok_demo_valid_123456"))
        assert res_demo["account_type"] == "DEMO"
        assert res_demo["is_virtual"] is True

    with patch(
        "app.services.deriv_service._ws_single_request",
        new=AsyncMock(
            return_value={
                "authorize": {
                    "loginid": "CR888222",
                    "currency": "USD",
                    "balance": 150.0,
                    "is_virtual": 0,
                    "scopes": ["read", "trade"],
                }
            }
        ),
    ):
        res_real = asyncio.run(verify_deriv_token("tok_real_valid_123456"))
        assert res_real["account_type"] == "REAL"
        assert res_real["is_virtual"] is False
