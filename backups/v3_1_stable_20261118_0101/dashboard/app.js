// app.js (v2.2) - Radar BTC M1 com Lock-on-Close Rigoroso e Anti-Cache
let ws = null;

function connectWebSocket() {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${proto}//${window.location.host}/ws/live`;
  
  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    const el = document.getElementById("wsStatus");
    if (el) {
      el.textContent = "Binance Live ⚡";
      el.style.color = "var(--accent-green)";
    }
  };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      updateDashboard(data);
    } catch (err) {
      console.error("Erro parsing WS:", err);
    }
  };

  ws.onclose = () => {
    const el = document.getElementById("wsStatus");
    if (el) {
      el.textContent = "Reconectando...";
      el.style.color = "var(--accent-red)";
    }
    setTimeout(connectWebSocket, 2000);
  };
}

function updateDashboard(data) {
  if (!data) return;

  // 1. Timer Regressivo M1
  const timerSec = data.seconds_left !== undefined ? data.seconds_left : 58;
  const timerEl = document.getElementById("timerSeconds");
  if (timerEl) {
    timerEl.textContent = timerSec < 10 ? `0${timerSec}` : `${timerSec}`;
    timerEl.style.color = timerSec <= 10 ? "var(--accent-gold)" : "var(--accent-cyan)";
  }

  // 2. Preço Atual
  if (data.current_price) {
    const pEl = document.getElementById("valPrice");
    if (pEl) {
      pEl.textContent = `$${data.current_price.toLocaleString("en-US", { minimumFractionDigits: 2 })}`;
    }
  }

  // 3. Painel de Viés Técnico (TradingView style)
  if (data.bias_summary) {
    const b = data.bias_summary;
    const elB = document.getElementById("biasBaixa");
    const elN = document.getElementById("biasNeutro");
    const elA = document.getElementById("biasAlta");
    if (elB) elB.textContent = b.baixa !== undefined ? b.baixa : 0;
    if (elN) elN.textContent = b.neutro !== undefined ? b.neutro : 0;
    if (elA) elA.textContent = b.alta !== undefined ? b.alta : 0;
  }

  // 4. Previsão Fixa da Vela Atual (Lock-on-Close / +5s)
  if (data.prediction) {
    const dir = data.prediction.direction || "WAIT";
    const isWait = (dir === "WAIT");
    const isCall = (dir === "CALL");
    const colorName = data.prediction.predicted_color || (isWait ? "AGUARDAR" : (isCall ? "VERDE" : "VERMELHA"));
    const colorClass = isWait ? "" : (isCall ? "verde" : "vermelha");
    const conf = data.prediction.confidence_pct || 50;

    // Vela Padrão
    const candleEl = document.getElementById("standardCandle");
    if (candleEl) {
      candleEl.className = colorClass ? `standard-candle ${colorClass}` : "standard-candle";
    }
    const dirTextEl = document.getElementById("candleDirText");
    if (dirTextEl) {
      dirTextEl.textContent = dir;
    }

    // Seta Hero
    const arrowEl = document.getElementById("heroArrow");
    if (arrowEl) {
      arrowEl.textContent = isWait ? "⏳" : (isCall ? "⬆" : "⬇");
      arrowEl.className = colorClass ? `hero-arrow ${colorClass}` : "hero-arrow";
    }

    // Badges de Confiança e Cor
    const confEl = document.getElementById("predConfidence");
    if (confEl) {
      confEl.textContent = `Confiança: ${conf}%`;
    }
    const colorBadge = document.getElementById("predColorText");
    if (colorBadge) {
      colorBadge.textContent = isWait ? colorName : `COR: ${colorName}`;
      colorBadge.style.color = isWait ? "var(--accent-gold)" : (isCall ? "var(--accent-green)" : "var(--accent-red)");
    }
  }

  // 5. Fluxo Real & CVD
  if (data.flow) {
    const cvdVal = data.flow.cvd_1m !== undefined ? data.flow.cvd_1m : 0.0;
    const cvdEl = document.getElementById("valCvd");
    if (cvdEl) {
      cvdEl.textContent = `${cvdVal > 0 ? "+" : ""}${cvdVal} BTC`;
      cvdEl.style.color = cvdVal >= 0 ? "var(--accent-green)" : "var(--accent-red)";
    }
    const aggEl = document.getElementById("valAggression");
    if (aggEl) {
      const ratio = data.flow.aggression_ratio || 1.0;
      const fbias = data.flow.flow_bias || "Equilibrado";
      aggEl.textContent = `${ratio}x (${fbias})`;
    }
  }

  // 6. Auditoria dos 120 Minutos
  if (data.audit_summary) {
    const aud = data.audit_summary;
    const tEl = document.getElementById("auditTotal");
    const wEl = document.getElementById("auditWins");
    const lEl = document.getElementById("auditLosses");
    if (tEl) tEl.textContent = aud.total || 0;
    if (wEl) wEl.textContent = aud.wins || 0;
    if (lEl) lEl.textContent = aud.losses || 0;

    const winrateEl = document.getElementById("auditWinrate");
    if (winrateEl) {
      const wr = aud.winrate || 0.0;
      winrateEl.textContent = `${wr}% Assertividade`;
      winrateEl.style.color = wr >= 54.6 ? "var(--accent-green)" : "var(--accent-gold)";
    }

    // Fita de Resultados Recentes
    if (aud.recent_results && aud.recent_results.length > 0) {
      const tape = document.getElementById("recentResultsTape");
      if (tape) {
        tape.innerHTML = "";
        aud.recent_results.slice().reverse().forEach(r => {
          const pill = document.createElement("div");
          pill.className = `result-pill ${r.result}`;
          pill.textContent = `[${r.time}] ${r.result === "WIN" ? "✅" : "❌"} ${r.predicted}`;
          tape.appendChild(pill);
        });
      }
    }
  }
}

document.addEventListener("DOMContentLoaded", () => {
  connectWebSocket();
});
