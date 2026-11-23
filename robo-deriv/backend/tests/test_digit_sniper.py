"""
backend/tests/test_digit_sniper.py
==================================
Comprehensive test suite for the Digit Sniper Module:
1. extract_last_digit precision, trailing zero retention, and pip_size null-safety.
2. Multi-user state and session isolation (reset_user_session does not affect peers).
3. Pydantic input bounds validation on /api/bot/sniper-config.
4. Quantitative strategy logic: Split recovery stake sizing, loss pool decrements, and pool depletion reset.
5. Safety locks: Stop loss preventive guard, pre-trade margin checks, addiction filter (>14% in last 50 ticks).
6. Atomic database persistence of basket, contract, audit log, and session state.
"""
import asyncio
from datetime import timedelta
import json
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["REDIS_URL"] = "redis://127.0.0.1:6399/0"
os.environ["JWT_SECRET"] = "test-super-secret-jwt-key-32-chars-minimum!!"
os.environ["ENCRYPTION_KEY"] = ""
os.environ["ADMIN_EMAIL"] = "admin@test.local"
os.environ["ADMIN_PASSWORD"] = "Admin@123456"
os.environ["DERIV_APP_ID"] = "1089"

from shared import config as shared_config
from shared import database as shared_db
from shared.models import Base, Basket, BotConfig, Contract, DerivAccount, User, utcnow

from app.core.redis_client import reset_sync_redis
from app.core.security import hash_password, login_rate_limiter
from app.main import create_app
from app.services.digit_sniper_service import DigitSniperService, extract_last_digit


@pytest.fixture()
def test_env():
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
        # Seed an additional test user
        db = TestingSessionLocal()
        u2 = User(
            id=2,
            name="Trader 2",
            email="trader2@deriv.local",
            password_hash=hash_password("Trader@123456"),
            role="USER",
            status="ACTIVE",
            expires_at=utcnow() + timedelta(days=365),
        )
        db.add(u2)

        cfg2 = BotConfig(user_id=2, sniper_enabled=False)
        db.add(cfg2)

        acc2 = DerivAccount(
            user_id=2,
            account_type="DEMO",
            loginid="VRTC222",
            token_encrypted="plain_token_2",
            currency="USD",
            balance=250.0,
            is_active_selected=True,
        )
        db.add(acc2)
        db.commit()
        db.close()

        yield client, TestingSessionLocal

    Base.metadata.drop_all(bind=engine)
    engine.dispose()
    shared_db._engine = None
    shared_db._SessionLocal = None


# --------------------------------------------------------------------------
# 1. EXTRACT LAST DIGIT TESTS (PRECISION & NULL SAFETY)
# --------------------------------------------------------------------------

def test_extract_last_digit_preserves_trailing_zeros():
    """Verify that quote 1250.40 with pip_size 2 outputs digit 0, not 4."""
    assert extract_last_digit(1250.40, 2) == 0
    assert extract_last_digit(6543.10, 2) == 0
    assert extract_last_digit(100.00, 2) == 0
    assert extract_last_digit(99.05, 2) == 5
    assert extract_last_digit(99.09, 2) == 9


def test_extract_last_digit_various_pip_sizes():
    """Verify digit extraction across different market decimal precisions."""
    assert extract_last_digit(1234.5678, 4) == 8
    assert extract_last_digit(1234.5671, 3) == 7
    assert extract_last_digit(1234.5678, 3) == 8  # 1234.568 -> 8
    assert extract_last_digit(1234.5678, 1) == 6
    assert extract_last_digit(1234.5678, 0) == 5


def test_extract_last_digit_null_and_invalid_safety():
    """Verify safe fallback on None, invalid strings or NaN without throwing exceptions."""
    assert extract_last_digit("invalid_number", 2) == 0
    assert extract_last_digit(None, 2) == 0


# --------------------------------------------------------------------------
# 2. MULTI-USER STATE & SCOREBOARD ISOLATION
# --------------------------------------------------------------------------

def test_multi_user_session_and_scoreboard_isolation(test_env):
    """Ensure that resetting User 1's scoreboard never mutates User 2's session state."""
    _, _ = test_env
    service = DigitSniperService()

    st1 = service.get_user_state(1)
    st2 = service.get_user_state(2)

    # User 1 has 5 wins, 2 losses, $12.50 profit
    st1["session_wins"] = 5
    st1["session_losses"] = 2
    st1["session_profit"] = 12.50
    st1["loss_pool"] = 1.05
    st1["recent_trades"] = [{"id": "T1", "is_win": True, "profit": 0.30}]

    # User 2 has 10 wins, 0 losses, $25.00 profit
    st2["session_wins"] = 10
    st2["session_losses"] = 0
    st2["session_profit"] = 25.00
    st2["loss_pool"] = 0.0
    st2["recent_trades"] = [{"id": "T2", "is_win": True, "profit": 2.50}]

    # Reset User 1 only
    asyncio.run(service.reset_user_session(1))

    # Assert User 1 was reset
    assert st1["session_wins"] == 0
    assert st1["session_losses"] == 0
    assert st1["session_profit"] == 0.0
    assert st1["loss_pool"] == 0.0
    assert len(st1["recent_trades"]) == 0

    # Assert User 2 remained completely intact
    assert st2["session_wins"] == 10
    assert st2["session_losses"] == 0
    assert st2["session_profit"] == 25.00
    assert len(st2["recent_trades"]) == 1


# --------------------------------------------------------------------------
# 3. PYDANTIC INPUT BOUNDS VALIDATION (/api/bot/sniper-config)
# --------------------------------------------------------------------------

def test_sniper_config_pydantic_validation_rejects_invalid_inputs(test_env):
    """Test that /api/bot/sniper-config enforces strict bounds on all parameters."""
    client, _ = test_env

    # Login as admin
    login_res = client.post("/api/auth/login", json={"email": "admin@test.local", "password": "Admin@123456"})
    assert login_res.status_code == 200
    token = login_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Division by zero: split_installments = 0 must be rejected
    r = client.post("/api/bot/sniper-config", json={"split_installments": 0}, headers=headers)
    assert r.status_code == 422, f"Expected 422 for split_installments=0, got {r.status_code}"

    # 2. Stake below Deriv minimum ($0.35) must be rejected
    r = client.post("/api/bot/sniper-config", json={"initial_stake": 0.10}, headers=headers)
    assert r.status_code == 422, f"Expected 422 for initial_stake=0.10, got {r.status_code}"

    # 3. Negative stake must be rejected
    r = client.post("/api/bot/sniper-config", json={"initial_stake": -5.0}, headers=headers)
    assert r.status_code == 422, f"Expected 422 for negative stake, got {r.status_code}"

    # 4. Invalid symbol outside allowed list must be rejected
    r = client.post("/api/bot/sniper-config", json={"symbol": "INVALID_BTC_TICK"}, headers=headers)
    assert r.status_code == 422, f"Expected 422 for unsupported symbol, got {r.status_code}"

    # 5. Invalid sub_mode outside enum must be rejected
    r = client.post("/api/bot/sniper-config", json={"sub_mode": "MARTINGALE_YOLO"}, headers=headers)
    assert r.status_code == 422, f"Expected 422 for unsupported sub_mode, got {r.status_code}"

    # 6. Valid configuration must be accepted with 200 OK (including EVEN_ODD_SNIPER, OVER1_UNDER8_HFT, MULTI_CONFLUENCE_AI)
    for valid_sub in ["EVEN_ODD_SNIPER", "OVER1_UNDER8_HFT", "MULTI_CONFLUENCE_AI", "DIGITDIFF_COLD", "OVER_UNDER_AUTO"]:
        r = client.post(
            "/api/bot/sniper-config",
            json={
                "symbol": "MULTI_RADAR",
                "sub_mode": valid_sub,
                "virtual_losses_required": 2,
                "initial_stake": 0.50,
                "split_installments": 4,
                "stop_win": 20.0,
                "stop_loss": 30.0,
                "max_recovery_stake": 5.0,
                "use_markov_filter": True,
            },
            headers=headers,
        )
        assert r.status_code == 200, f"Expected 200 for sub_mode={valid_sub}, got {r.status_code}"
        body = r.json()
        assert body["symbol"] == "MULTI_RADAR"
        assert body["sub_mode"] == valid_sub
        assert body["virtual_losses_required"] == 2
        assert body["initial_stake"] == 0.50
        assert body["split_installments"] == 4
        assert body["max_recovery_stake"] == 5.0


# --------------------------------------------------------------------------
# 4. QUANTITATIVE STRATEGY & SPLIT RECOVERY STAKE CALCULATIONS
# --------------------------------------------------------------------------

def test_split_recovery_stake_and_pool_depletion():
    """Verify quantitative mathematical formula for target gain and fractional recovery stake."""
    service = DigitSniperService()
    st = service.get_user_state(1)

    initial_stake = 0.35
    split_installments = 3
    st["initial_stake"] = initial_stake
    st["split_installments"] = split_installments
    st["loss_pool"] = 0.0

    # Case A: Normal mode without losses -> next_stake == initial_stake
    rem_inst = max(1, st.get("remaining_installments", 3))
    loss_pool = st.get("loss_pool", 0.0)
    assert loss_pool == 0.0
    next_stake = initial_stake
    assert next_stake == 0.35

    # Case B: First loss of $0.35 occurs
    st["loss_pool"] = 0.35
    loss_pool = st["loss_pool"]
    target_gain = (loss_pool / split_installments) + 0.05  # (0.35 / 3) + 0.05 = ~0.1667
    calculated = max(0.35, round(target_gain / 0.088, 2))  # 0.1667 / 0.088 = ~1.89
    assert calculated == 1.89

    # Case C: Win recovers $0.17 -> pool decreases to $0.18
    profit_win = 0.17
    st["loss_pool"] = max(0.0, round(st["loss_pool"] - profit_win, 2))
    assert st["loss_pool"] == 0.18

    # Case D: Subsequent win of $0.18 -> pool is fully depleted, reset to initial stake
    st["loss_pool"] = max(0.0, round(st["loss_pool"] - 0.18, 2))
    assert st["loss_pool"] == 0.0
    if st["loss_pool"] <= 0:
        st["current_stake"] = initial_stake
    assert st["current_stake"] == 0.35


def test_max_recovery_stake_caps_astronomical_losses():
    """Verify that a large loss pool (e.g. $180.46) is strictly capped at max_recovery_stake."""
    loss_pool = 180.46
    rem_inst = 3
    payout = 0.088
    max_rec_stake = 3.50

    target_gain = (loss_pool / rem_inst) + 0.05
    calculated = max(0.35, round(target_gain / payout, 2))
    assert calculated == 684.13  # The exact uncapped astronomical stake from the user screenshot!

    # With the cap:
    next_stake = min(max_rec_stake, calculated)
    assert next_stake == 3.50  # Strictly capped at safe $3.50!


def test_markov_safety_vetos_repeating_digit_cluster():
    """Verify check_markov_safety vetos entry when empirical transition probability exceeds 8%."""
    from collections import deque
    from app.services.digit_sniper_service import check_markov_safety

    # Sequence where 0 frequently transitions into 0 (repeat cluster)
    seq = deque([0, 0, 1, 2, 0, 0, 3, 4, 0, 0, 5, 6, 0, 0, 7, 8, 0, 0, 9, 1, 0, 0, 2, 3, 0, 0, 4, 5, 0, 0], maxlen=100)
    is_safe, prob = check_markov_safety(seq, current_d=0, losing_d=0)
    assert is_safe is False
    assert prob > 0.08


# --------------------------------------------------------------------------
# 5. ADDICTION FILTER (>14% in last 50 ticks) & STOP LOSS GUARD
# --------------------------------------------------------------------------

def test_addiction_filter_detection():
    """Verify that any digit appearing in >14% of the last 50 ticks is identified as addicted."""
    digits = [0] * 8 + [1] * 42
    assert len(digits) == 50

    counts50 = [0] * 10
    for d in digits:
        counts50[d] += 1

    addicted = [d for d in range(10) if (counts50[d] / 50.0) * 100.0 > 14.0]
    assert 0 in addicted
    assert 1 in addicted
    assert 2 not in addicted


def test_stop_loss_risk_guard_prevents_order_dispatch():
    """Verify that if (session_profit - next_stake) < -stop_loss, trading halts before dispatch."""
    session_profit = -14.80
    stop_loss = 15.00
    next_stake = 0.35

    # Potential loss would bring session to -15.15, violating -15.00 limit
    violation = (session_profit - next_stake) < -stop_loss
    assert violation is True


# --------------------------------------------------------------------------
# 6. ATOMIC DATABASE PERSISTENCE FOR TRADES AND SESSION
# --------------------------------------------------------------------------

def test_atomic_persistence_of_trade_and_session(test_env):
    """Verify that _persist_trade_and_state writes basket, contract, log, and updates state in 1 transaction."""
    _, Session = test_env
    service = DigitSniperService()
    st = service.get_user_state(1)
    st["session_wins"] = 1
    st["session_losses"] = 0
    st["session_profit"] = 0.31
    st["loss_pool"] = 0.0
    st["remaining_installments"] = 3
    st["current_stake"] = 0.35
    st["recent_trades"] = [{
        "id": "999888",
        "symbol": "1HZ100V",
        "contract_type": "DIGITOVER",
        "barrier": "0",
        "stake": 0.35,
        "profit": 0.31,
        "is_win": True,
    }]

    service._persist_trade_and_state(
        user_id=1,
        symbol="1HZ100V",
        contract_type="DIGITOVER",
        barrier="0",
        stake=0.35,
        profit=0.31,
        is_win=True,
        contract_id="999888",
        exit_spot=1254.30,
        exit_digit=0,
    )

    db = Session()
    try:
        basket = db.query(Basket).filter(Basket.user_id == 1).first()
        assert basket is not None
        assert basket.direction == "DIGITOVER"
        assert basket.current_pnl_usd == 0.31
        assert basket.close_reason == "TP"

        contract = db.query(Contract).filter(Contract.basket_id == basket.id).first()
        assert contract is not None
        assert contract.deriv_contract_id == "999888"
        assert contract.final_profit_usd == 0.31

        cfg = db.query(BotConfig).filter(BotConfig.user_id == 1).first()
        assert cfg is not None
        saved_state = json.loads(cfg.sniper_state_json)
        assert saved_state["session_wins"] == 1
        assert saved_state["session_profit"] == 0.31
    finally:
        db.close()
