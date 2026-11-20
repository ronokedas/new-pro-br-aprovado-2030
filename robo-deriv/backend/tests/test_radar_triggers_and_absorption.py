"""
backend/tests/test_radar_triggers_and_absorption.py
===================================================
Testes unitários e de integração para o Radar BTC M1:
1. Absorção em Suporte/Resistência (sr_absorption.py)
2. Regra Anti-Contradição Institucional (RealtimePredictor)
3. Endpoints de sincronização de configuração de gatilhos (/api/btc-radar/triggers-config)
"""
import pytest
from app.radar.engine.predictor import RealtimePredictor
from app.radar.engine.sr_absorption import detect_sr_absorption, extract_pivots


def test_extract_pivots_support_and_resistance():
    # Cria uma série sintética de velas com pivôs claros
    candles = []
    base_price = 60000.0
    for i in range(30):
        # Cria topo em i=10 e fundo em i=20
        if i == 10:
            h, l, c, o = 62000.0, 61500.0, 61800.0, 61600.0
        elif i == 20:
            h, l, c, o = 59000.0, 58000.0, 58200.0, 58900.0
        else:
            h, l, c, o = 60500.0, 59500.0, 60000.0, 60000.0
        candles.append({
            "open_time": i * 60000,
            "close_time": (i + 1) * 60000 - 1,
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": 10.0,
            "taker_buy_base": 5.0,
        })

    res, sup = extract_pivots(candles, current_price=60000.0)
    assert any(abs(r - 62000.0) < 50 for r in res), "Resistência fractal em 62000 não detectada"
    assert any(abs(s - 58000.0) < 50 for s in sup), "Suporte fractal em 58000 não detectado"


def test_detect_sr_absorption_bullish_support_bounce():
    # Simula suporte em $60,000.00
    candles = []
    for i in range(25):
        # Fundo prévio em i=5
        if i == 5:
            l, h, o, c = 60000.0, 60400.0, 60300.0, 60200.0
        elif i == 24:
            # Vela atual fechou testando o suporte de 60000 com rejeição (pavio inferior) e CVD positivo
            l, h, o, c = 59980.0, 60300.0, 60200.0, 60250.0  # pavio inf = (60200 - 59980) / 320 = ~68%
        else:
            l, h, o, c = 60200.0, 60800.0, 60500.0, 60400.0

        candles.append({
            "open_time": i * 60000,
            "close_time": (i + 1) * 60000 - 1,
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": 15.0,
            "taker_buy_base": 12.0,  # CVD positivo (+9 BTC)
        })

    l2_snapshot = {
        "ready": True,
        "imb5_spot": 0.25,
        "ofi_spot_5s": 0.30,
        "regime": "ABSORCAO_REVERSAO",
        "max_bid_wall": {"price": 60000.0, "qty_btc": 15.5},
    }
    indicators_vote = {
        "summary": {"call": 5, "put": 2, "neutro": 2, "verdict": "CALL"}
    }

    result = detect_sr_absorption(candles, l2_snapshot=l2_snapshot, indicators_vote=indicators_vote)
    assert result is not None, "Absorção em suporte deveria ter sido detectada"
    assert result["direction"] == "CALL", "Deveria gerar CALL em absorção compradora de suporte"
    assert "SUPORTE" in result["predicted_color"].upper()
    assert result["confidence_pct"] >= 68


def test_anti_contradiction_rule_vetoes_put_when_majority_bullish():
    predictor = RealtimePredictor(primary_symbol="BTCUSDT")
    predictor.update_triggers_config({"enableInstitutional10": True, "enableAbsorptionSR": False, "enableProductA": True})

    # Cria candles onde a base Z-score ficaria negativa (tenta gerar PUT), mas maioria dos indicadores está em ALTA
    spot_candles = []
    perp_candles = []
    base_ts = 1700000000000

    # 100 velas com Spot acima de Perp para dar z negativo no basis
    for i in range(100):
        t_open = base_ts + i * 60000
        t_close = t_open + 59999
        s_close = 60000.0 + i * 2.0  # Spot subindo (gerando indicadores técnicos em ALTA)
        p_close = s_close - 50.0     # Perp abaixo de Spot (Basis negativo)
        spot_candles.append({
            "open_time": t_open,
            "close_time": t_close,
            "open": s_close - 1.0,
            "high": s_close + 5.0,
            "low": s_close - 2.0,
            "close": s_close,
            "volume": 20.0,
            "taker_buy_base": 16.0,
        })
        perp_candles.append({
            "open_time": t_open,
            "close_time": t_close,
            "open": p_close - 1.0,
            "high": p_close + 5.0,
            "low": p_close - 2.0,
            "close": p_close,
            "volume": 20.0,
        })

    next_min_ts = base_ts + 100 * 60000
    fast_snap = {
        "binance_spot_mid": 60200.0,
        "binance_perp_mid": 60100.0,
        "server_now_ms": next_min_ts + 50,
        "ages_ms": {"binance_spot": 10, "binance_perp": 10},
    }

    pred = predictor.lock_at_minute_open(
        minute_open_ms=next_min_ts,
        spot_history=spot_candles,
        perp_history=perp_candles,
        fast_snapshot=fast_snap,
        l2_snapshot={"ready": True, "imb5_spot": 0.1, "ofi_spot_5s": 0.1, "regime": "NEUTRO"},
        auditable=False,
    )

    # Com enableInstitutional10 ativo e tendência/indicadores de alta, o sinal NUNCA pode ser PUT!
    assert pred["direction"] != "PUT", "Sinal não pode ser PUT contra maioria dos indicadores em alta!"
    if pred["direction"] == "WAIT":
        assert "VETADO" in pred["predicted_color"] or "INDICADORES" in pred["predicted_color"]


def test_default_triggers_pure_strategy_intact():
    predictor = RealtimePredictor(primary_symbol="BTCUSDT")
    # Padrão comprovado deve ter apenas Produto A, B e Veto L2 ligados
    assert predictor.triggers_cfg["enableProductA"] is True
    assert predictor.triggers_cfg["enableProductB"] is True
    assert predictor.triggers_cfg["enableL2Veto"] is True
    assert predictor.triggers_cfg["enableM5Filter"] is False
    assert predictor.triggers_cfg["enableMicro15s"] is False
    assert predictor.triggers_cfg["enableInstitutional10"] is False
    assert predictor.triggers_cfg["enableAbsorptionSR"] is False

    # Altera e reseta
    predictor.update_triggers_config({"enableInstitutional10": True, "enableAbsorptionSR": True})
    assert predictor.triggers_cfg["enableInstitutional10"] is True
    res = predictor.reset_to_default_triggers()
    assert res["enableInstitutional10"] is False
    assert res["enableAbsorptionSR"] is False
    assert res["enableProductA"] is True
    assert res["enableProductB"] is True
    assert res["enableL2Veto"] is True
