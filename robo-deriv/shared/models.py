from datetime import datetime, timezone
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(120), nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default="USER")  # ADMIN | USER
    status = Column(String(20), nullable=False, default="ACTIVE")  # ACTIVE | SUSPENDED
    expires_at = Column(DateTime(timezone=True), nullable=False)
    max_stake_usd = Column(Float, nullable=False, default=50.0)
    can_access_btc_radar = Column(Boolean, nullable=False, default=True)
    can_access_robo_deriv = Column(Boolean, nullable=False, default=True)
    allowed_modules_json = Column(Text, nullable=False, default='["radar_btc", "robo_deriv"]')
    current_session_id = Column(String(64), nullable=True)
    current_session_ip = Column(String(64), nullable=True)
    must_change_password = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    deriv_accounts = relationship("DerivAccount", back_populates="user", cascade="all, delete-orphan")
    bot_config = relationship("BotConfig", back_populates="user", uselist=False, cascade="all, delete-orphan")
    baskets = relationship("Basket", back_populates="user", cascade="all, delete-orphan")


class DerivAccount(Base):
    __tablename__ = "deriv_accounts"
    __table_args__ = (UniqueConstraint("user_id", "loginid", name="uq_user_loginid"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    loginid = Column(String(64), nullable=False)  # e.g., VRTC123456 or CR123456
    account_type = Column(String(16), nullable=False, default="DEMO")  # DEMO | REAL
    currency = Column(String(16), nullable=False, default="USD")
    token_encrypted = Column(Text, nullable=False)
    balance = Column(Float, nullable=False, default=0.0)
    is_active_selected = Column(Boolean, nullable=False, default=False)
    last_synced_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    user = relationship("User", back_populates="deriv_accounts")


class BotConfig(Base):
    __tablename__ = "bot_configs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    enabled = Column(Boolean, nullable=False, default=False)
    symbol = Column(String(64), nullable=False, default="cryBTCUSD")
    timeframe_sec = Column(Integer, nullable=False, default=300)  # 60, 300 (M5), 900 (M15), 3600 (H1)
    direction_mode = Column(String(20), nullable=False, default="LONG_ONLY")  # LONG_ONLY | SHORT_ONLY | BOTH
    stake_usd = Column(Float, nullable=False, default=1.0)
    multiplier = Column(Integer, nullable=False, default=100)
    max_layers = Column(Integer, nullable=False, default=3)
    layer_multipliers_json = Column(Text, nullable=False, default="[1.0, 1.0, 1.5]")
    basket_tp_usd = Column(Float, nullable=False, default=0.15)
    trailing_enabled = Column(Boolean, nullable=False, default=True)
    max_basket_loss_usd = Column(Float, nullable=False, default=2.50)
    max_daily_loss_usd = Column(Float, nullable=False, default=10.0)
    daily_profit_target_usd = Column(Float, nullable=False, default=15.0)
    max_consecutive_losses = Column(Integer, nullable=False, default=3)
    max_hold_minutes = Column(Integer, nullable=False, default=180)
    use_binance_filter = Column(Boolean, nullable=False, default=True)
    strategy_mode = Column(String(32), nullable=False, default="SMART_CHANNEL_DCA")  # SMART_CHANNEL_DCA | LINREG_ATR_VIDEO
    channel_window = Column(Integer, nullable=False, default=60)
    channel_sigma = Column(Float, nullable=False, default=2.0)
    atr_period = Column(Integer, nullable=False, default=10)
    tp_atr_mult = Column(Float, nullable=False, default=0.75)
    sl_atr_mult = Column(Float, nullable=False, default=1.90)
    use_crash_guard = Column(Boolean, nullable=False, default=True)
    multi_pair_enabled = Column(Boolean, nullable=False, default=False)
    scanner_symbols_json = Column(Text, nullable=False, default='["cryBTCUSD", "cryETHUSD", "frxEURUSD", "frxGBPUSD", "frxUSDJPY", "1HZ100V", "R_75"]')
    session_profit_usd = Column(Float, nullable=False, default=0.0)
    session_cycles_count = Column(Integer, nullable=False, default=0)
    grid_step_pct = Column(Float, nullable=False, default=0.25)
    deep_analysis_enabled = Column(Boolean, nullable=False, default=True)
    conservative_mode_enabled = Column(Boolean, nullable=False, default=True)
    tick_duration = Column(Integer, nullable=False, default=1)  # 1m or 1-5 ticks
    martingale_factor = Column(Float, nullable=False, default=2.15)
    active_indices_json = Column(Text, nullable=False, default='["cryETHUSD", "cryBTCUSD", "JD75", "R_100", "1HZ100V", "R_50", "JD50"]')
    status_text = Column(String(40), nullable=False, default="OFF")
    sniper_enabled = Column(Boolean, nullable=False, default=False)
    sniper_config_json = Column(Text, nullable=False, default='{}')
    sniper_state_json = Column(Text, nullable=False, default='{}')
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    user = relationship("User", back_populates="bot_config")


class Basket(Base):
    __tablename__ = "baskets"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    deriv_account_id = Column(Integer, ForeignKey("deriv_accounts.id", ondelete="SET NULL"), nullable=True)
    account_type = Column(String(16), nullable=False, default="DEMO")  # DEMO | REAL
    symbol = Column(String(64), nullable=False, index=True)
    direction = Column(String(16), nullable=False, default="MULTUP")  # MULTUP | MULTDOWN
    status = Column(String(20), nullable=False, default="OPEN", index=True)  # OPEN | CLOSED
    layers_count = Column(Integer, nullable=False, default=0)
    total_stake_usd = Column(Float, nullable=False, default=0.0)
    total_commission_usd = Column(Float, nullable=False, default=0.0)
    avg_entry_price = Column(Float, nullable=False, default=0.0)
    current_pnl_usd = Column(Float, nullable=False, default=0.0)
    realized_pnl_usd = Column(Float, nullable=False, default=0.0)
    peak_pnl_usd = Column(Float, nullable=False, default=0.0)
    close_reason = Column(String(40), nullable=True)  # TP | TRAILING | STOP_LOSS | CHANNEL_BREAK | TIMEOUT | KILL_SWITCH | STOP_OUT | MANUAL
    opened_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="baskets")
    contracts = relationship("Contract", back_populates="basket", cascade="all, delete-orphan")


class Contract(Base):
    __tablename__ = "contracts"

    id = Column(Integer, primary_key=True, index=True)
    basket_id = Column(Integer, ForeignKey("baskets.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    layer_index = Column(Integer, nullable=False, default=1)
    deriv_contract_id = Column(String(64), unique=True, nullable=True, index=True)
    contract_type = Column(String(16), nullable=False, default="MULTUP")
    symbol = Column(String(64), nullable=False)
    stake_usd = Column(Float, nullable=False, default=1.0)
    multiplier = Column(Integer, nullable=False, default=100)
    commission_usd = Column(Float, nullable=False, default=0.0)
    entry_spot = Column(Float, nullable=False, default=0.0)
    stop_out_spot = Column(Float, nullable=True)
    current_spot = Column(Float, nullable=True)
    current_profit_usd = Column(Float, nullable=False, default=0.0)
    exit_spot = Column(Float, nullable=True)
    final_profit_usd = Column(Float, nullable=True)
    status = Column(String(20), nullable=False, default="OPEN")  # OPEN | SOLD | STOPPED_OUT
    opened_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    closed_at = Column(DateTime(timezone=True), nullable=True)

    basket = relationship("Basket", back_populates="contracts")


class BotEventLog(Base):
    __tablename__ = "bot_event_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    level = Column(String(16), nullable=False, default="INFO")  # INFO | SIGNAL | TRADE | WARN | ERROR
    symbol = Column(String(64), nullable=True)
    message = Column(Text, nullable=False)
    details_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
