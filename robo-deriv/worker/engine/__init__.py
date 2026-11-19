"""Quantitative Multi-User Deriv DCA Trading Engine."""

from engine.binance_lead import BinanceLeadFilter
from engine.channel_detector import ChannelDetector, ChannelSnapshot
from engine.confirmations import SignalDecision, dca_reentry_signal, entry_signal
from engine.coordinator import WorkerCoordinator
from engine.dca_basket_manager import (
    BasketExitDecision,
    BasketState,
    ContractSnapshot,
    SmartDCABasketManager,
)
from engine.deriv_client import DerivAPIError, DerivClient, extract_stop_out_pct
from engine.market_hub import MarketHub
from engine.risk_guard import RiskConfig, RiskGuard, RiskVerdict, UserAccessState
from engine.user_session import UserSession

__all__ = [
    "BinanceLeadFilter",
    "ChannelDetector",
    "ChannelSnapshot",
    "SignalDecision",
    "entry_signal",
    "dca_reentry_signal",
    "SmartDCABasketManager",
    "BasketState",
    "ContractSnapshot",
    "BasketExitDecision",
    "DerivClient",
    "DerivAPIError",
    "extract_stop_out_pct",
    "MarketHub",
    "RiskConfig",
    "RiskGuard",
    "RiskVerdict",
    "UserAccessState",
    "UserSession",
    "WorkerCoordinator",
]
