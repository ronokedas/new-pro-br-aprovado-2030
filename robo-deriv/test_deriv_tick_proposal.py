import asyncio
import json
import httpx
from shared.database import session_scope
from shared.models import DerivAccount
from shared.crypto_vault import decrypt_secret
from shared.config import get_settings
from worker.engine.deriv_client import DerivClient

async def run_test():
    with session_scope() as db:
        acc = db.query(DerivAccount).filter_by(user_id=1).first()
        if not acc:
            print("Nenhuma conta encontrada")
            return
        token = decrypt_secret(acc.token_encrypted)
        loginid = acc.loginid

    print(f"Conta encontrada: {loginid}")

    # Obter OTP WS URL
    otp_endpoint = f"https://api.derivws.com/trading/v1/options/accounts/{loginid}/otp"
    async with httpx.AsyncClient(timeout=10.0) as http_client:
        otp_resp = await http_client.post(
            otp_endpoint,
            json={},
            headers={
                "Authorization": f"Bearer {token}",
                "Deriv-App-ID": "34ZYYnkkxGGXQTva4v3lB",
                "User-Agent": "Mozilla/5.0",
            },
        )
        if otp_resp.status_code != 200:
            print(f"Erro OTP: {otp_resp.text}")
            return
        ws_url = otp_resp.json().get("data", {}).get("url")

    print(f"OTP WS URL obtida: {ws_url[:40]}...")

    client = DerivClient(ws_url=ws_url, token=token, label="test-tick")
    await client.connect(timeout=15.0)
    print("Conectado ao WS autenticado!")

    # Teste 1: Proposta de Rise/Fall (CALL) em Ticks no Jump 25 Index (JD25)
    p_call = {
        "proposal": 1,
        "amount": 0.60,
        "basis": "stake",
        "contract_type": "CALL",
        "currency": "USD",
        "duration": 5,
        "duration_unit": "t",
        "underlying_symbol": "JD25"
    }
    resp1 = await client.send_request(p_call, timeout=10.0)
    print("PROPOSAL 1 (JD25 CALL 5 ticks):", json.dumps(resp1, indent=2))

    # Teste 2: Proposta em Bull Market Index (RDBULL)
    p_bull = {
        "proposal": 1,
        "amount": 0.60,
        "basis": "stake",
        "contract_type": "CALL",
        "currency": "USD",
        "duration": 5,
        "duration_unit": "t",
        "underlying_symbol": "RDBULL"
    }
    resp2 = await client.send_request(p_bull, timeout=10.0)
    print("PROPOSAL 2 (RDBULL CALL 5 ticks):", json.dumps(resp2, indent=2))

    # Teste 3: Proposta de Dígitos (DIGITOVER) no Volatility 100
    p_digit = {
        "proposal": 1,
        "amount": 0.60,
        "basis": "stake",
        "contract_type": "DIGITOVER",
        "currency": "USD",
        "duration": 1,
        "duration_unit": "t",
        "barrier": "2",
        "underlying_symbol": "R_100"
    }
    resp3 = await client.send_request(p_digit, timeout=10.0)
    print("PROPOSAL 3 (R_100 DIGITOVER 2):", json.dumps(resp3, indent=2))

    await client.close()

if __name__ == "__main__":
    asyncio.run(run_test())
