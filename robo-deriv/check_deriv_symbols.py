import asyncio
import json
import websockets

async def check():
    uri = "wss://ws.derivws.com/websockets/v3?app_id=1089"
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({"active_symbols": "brief", "product_type": "basic"}))
        resp = json.loads(await ws.recv())
        symbols = resp.get("active_symbols", [])
        jump = [s for s in symbols if "jump" in s.get("display_name", "").lower() or "bull" in s.get("display_name", "").lower() or "bear" in s.get("display_name", "").lower()]
        for s in jump:
            print("SYMBOL:", s.get("symbol"), "| NAME:", s.get("display_name"), "| MARKET:", s.get("market_display_name"))

asyncio.run(check())
