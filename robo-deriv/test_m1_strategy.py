import asyncio
import json
import time
import websockets

def compute_sma(series, period):
    if len(series) < period:
        return None
    return sum(series[-period:]) / float(period)

def compute_rsi(closes, period=5):
    if len(closes) < period + 1:
        return None
    diffs = [closes[i] - closes[i - 1] for i in range(-period, 0)]
    gains = [d for d in diffs if d > 0]
    losses = [abs(d) for d in diffs if d < 0]
    avg_gain = sum(gains) / float(period)
    avg_loss = sum(losses) / float(period)
    if avg_loss <= 1e-8:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

async def test_symbol(symbol):
    uri = "wss://api.derivws.com/trading/v1/options/ws/public"
    async with websockets.connect(uri) as ws:
        # Request 300 M1 candles (granularity = 60)
        req = {
            "ticks_history": symbol,
            "style": "candles",
            "granularity": 60,
            "count": 300,
            "end": "latest"
        }
        await ws.send(json.dumps(req))
        resp = json.loads(await ws.recv())
        candles = resp.get("candles", [])
        if not candles:
            print(f"[{symbol}] Sem velas retornadas: {resp}")
            return

        closes = [float(c["close"]) for c in candles]
        last_c = candles[-1]
        c_open = float(last_c["open"])
        c_close = float(last_c["close"])
        c_high = float(last_c["high"])
        c_low = float(last_c["low"])
        c_time = int(last_c["epoch"])

        ma6 = compute_sma(closes, 6)
        ma14 = compute_sma(closes, 14)
        ma250 = compute_sma(closes, min(250, len(closes)))
        rsi5 = compute_rsi(closes, 5)

        is_green = c_close >= c_open
        is_red = c_close < c_open

        # Trigger logic
        call_signal = (ma6 > ma14) and (c_close > ma250) and (rsi5 > 50) and is_green
        put_signal = (ma6 < ma14) and (c_close < ma250) and (rsi5 < 50) and is_red

        direction = "CALL (MULTUP)" if call_signal else ("PUT (MULTDOWN)" if put_signal else "NEUTRO")

        print(f"[{symbol}] Velas: {len(candles)} | Close: {c_close:.4f} | MA6: {ma6:.4f} | MA14: {ma14:.4f} | MA250: {ma250:.4f} | RSI5: {rsi5:.1f} | Sinal: {direction}")

async def main():
    print("--- Testando Indicadores Katie Tutorials em Tempo Real na Deriv ---")
    for sym in ["cryBTCUSD", "frxEURUSD", "frxUSDJPY", "1HZ100V", "JD25"]:
        await test_symbol(sym)

if __name__ == "__main__":
    asyncio.run(main())
