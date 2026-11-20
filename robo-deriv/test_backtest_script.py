import asyncio
import json
import websockets

def calculate_sma(prices, period):
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period

def calculate_rsi(prices, period=5):
    if len(prices) < period + 1:
        return 50.0
    changes = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [max(c, 0.0) for c in changes[-period:]]
    losses = [max(-c, 0.0) for c in changes[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

DISPLAY_NAMES = {
    "frxEURUSD": ("EUR/USD", "Forex"),
    "frxUSDJPY": ("USD/JPY", "Forex"),
    "frxGBPUSD": ("GBP/USD", "Forex"),
    "frxAUDUSD": ("AUD/USD", "Forex"),
    "frxUSDCAD": ("USD/CAD", "Forex"),
    "frxUSDCHF": ("USD/CHF", "Forex"),
    "frxEURJPY": ("EUR/JPY", "Forex"),
    "frxGBPJPY": ("GBP/JPY", "Forex"),
    "frxEURGBP": ("EUR/GBP", "Forex"),
    "frxAUDJPY": ("AUD/JPY", "Forex"),
    "cryBTCUSD": ("Bitcoin / USD", "Cripto"),
    "cryETHUSD": ("Ethereum / USD", "Cripto"),
    "1HZ100V": ("Volatility 100 (1s)", "Sintéticos"),
    "R_100": ("Volatility 100", "Sintéticos"),
    "R_50": ("Volatility 50", "Sintéticos"),
    "R_25": ("Volatility 25", "Sintéticos"),
    "R_10": ("Volatility 10", "Sintéticos"),
    "JD25": ("Jump 25 Index", "Sintéticos"),
    "JD50": ("Jump 50 Index", "Sintéticos"),
    "JD75": ("Jump 75 Index", "Sintéticos"),
}

async def run_backtest_symbol(ws, sym, count=800):
    req = {
        'ticks_history': sym,
        'adjust_start_time': 1,
        'count': count,
        'end': 'latest',
        'style': 'candles',
        'granularity': 60
    }
    await ws.send(json.dumps(req))
    resp = json.loads(await ws.recv())
    candles = resp.get('candles', [])
    if len(candles) < 260:
        return None

    closes = [c['close'] for c in candles]

    trades = []
    in_trade = False
    direction = None
    entry_price = 0.0
    avg_price = 0.0
    layers = 0
    total_stake = 0.0
    trade_start_i = 0
    commission = 0.08
    mult = 100

    # Start after 250 candles warmup
    for i in range(250, len(candles)):
        c_curr = candles[i]
        close_curr = c_curr['close']
        
        hist_curr = closes[:i + 1]
        hist_prev = closes[:i]

        ma250 = calculate_sma(hist_curr, 250)
        ma14_curr = calculate_sma(hist_curr, 14)
        ma14_prev = calculate_sma(hist_prev, 14)
        ma6_curr = calculate_sma(hist_curr, 6)
        ma6_prev = calculate_sma(hist_prev, 6)
        rsi5_curr = calculate_rsi(hist_curr, 5)

        # In trade check
        if in_trade:
            # Check re-entry on adverse candle close (Layer 2)
            if layers == 1:
                is_adverse = (direction == 'CALL' and close_curr < entry_price) or (direction == 'PUT' and close_curr > entry_price)
                if is_adverse:
                    layers = 2
                    stake2 = 2.0
                    avg_price = (1.0 * entry_price + stake2 * close_curr) / 3.0
                    total_stake = 3.0

            # Calculate PnL relative to avg_price
            if direction == 'CALL':
                pnl = total_stake * mult * ((c_curr['high'] - avg_price) / avg_price) - (layers * commission)
                loss_pnl = total_stake * mult * ((c_curr['low'] - avg_price) / avg_price) - (layers * commission)
            else:
                pnl = total_stake * mult * ((avg_price - c_curr['low']) / avg_price) - (layers * commission)
                loss_pnl = total_stake * mult * ((avg_price - c_curr['high']) / avg_price) - (layers * commission)

            # Target win (+$0.50) or Stop Loss (-$10) or Max hold 12 candles
            if pnl >= 0.50:
                trades.append({'win': True, 'pnl': 0.50, 'layers': layers})
                in_trade = False
            elif loss_pnl <= -10.0 or (i - trade_start_i >= 12):
                final_pnl = total_stake * mult * ((close_curr - avg_price) / avg_price if direction == 'CALL' else (avg_price - close_curr) / avg_price) - (layers * commission)
                trades.append({'win': final_pnl > 0, 'pnl': final_pnl, 'layers': layers})
                in_trade = False
            continue

        # Check triggers
        # Long: Close > MA250, MA6 crosses above MA14, RSI5 >= 60
        call_cross = (ma6_prev is not None and ma14_prev is not None and ma6_prev <= ma14_prev) and (ma6_curr > ma14_curr)
        if close_curr > ma250 and call_cross and rsi5_curr >= 60:
            in_trade = True
            direction = 'CALL'
            entry_price = close_curr
            avg_price = close_curr
            layers = 1
            total_stake = 1.0
            trade_start_i = i
            continue

        # Short: Close < MA250, MA6 crosses below MA14, RSI5 <= 40
        put_cross = (ma6_prev is not None and ma14_prev is not None and ma6_prev >= ma14_prev) and (ma6_curr < ma14_curr)
        if close_curr < ma250 and put_cross and rsi5_curr <= 40:
            in_trade = True
            direction = 'PUT'
            entry_price = close_curr
            avg_price = close_curr
            layers = 1
            total_stake = 1.0
            trade_start_i = i
            continue

    total = len(trades)
    wins = sum(1 for t in trades if t['win'])
    losses = total - wins
    wr = (wins / total * 100) if total > 0 else 0.0
    tot_pnl = sum(t['pnl'] for t in trades)
    d_name, cat = DISPLAY_NAMES.get(sym, (sym, 'Outros'))
    return {
        'symbol': sym,
        'name': d_name,
        'category': cat,
        'candles': len(candles),
        'trades': total,
        'wins': wins,
        'losses': losses,
        'winrate': round(wr, 1),
        'pnl': round(tot_pnl, 2)
    }

async def main():
    CANDIDATES = [
        'frxEURUSD', 'frxUSDJPY', 'frxGBPUSD', 'frxAUDUSD', 'frxUSDCAD',
        'frxUSDCHF', 'frxEURJPY', 'frxGBPJPY', 'frxEURGBP', 'frxAUDJPY',
        'cryBTCUSD', 'cryETHUSD',
        '1HZ100V', 'R_100', 'R_50', 'R_25', 'R_10', 'JD25', 'JD50', 'JD75'
    ]
    results = []
    print("Conectando ao Deriv WS e baixando velas reais M1...")
    async with websockets.connect('wss://api.derivws.com/trading/v1/options/ws/public') as ws:
        for s in CANDIDATES:
            r = await run_backtest_symbol(ws, s, count=1000)
            if r:
                results.append(r)
                print(f"[{r['category']}] {r['name']:18s} ({r['symbol']:9s}): {r['trades']:2d} trades | Winrate: {r['winrate']:5.1f}% | PnL: ${r['pnl']:+6.2f}")

    results.sort(key=lambda x: (x['winrate'], x['pnl']), reverse=True)
    print("\n" + "="*80)
    print("RANKING TOP 10 MAIS ASSERTIVOS (AUDITORIA REAL DERIV M1)")
    print("="*80)
    for idx, r in enumerate(results[:10], 1):
        print(f"#{idx:02d} | {r['name']:18s} | {r['symbol']:9s} | {r['category']:10s} | {r['trades']:2d} trades ({r['wins']}W/{r['losses']}L) | {r['winrate']:5.1f}% Winrate | PnL: ${r['pnl']:+6.2f}")

if __name__ == '__main__':
    asyncio.run(main())
