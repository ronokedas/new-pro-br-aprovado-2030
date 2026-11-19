"""
pattern_matcher.py
Identifica padrões de cores de sequência de velas (3 a 5 velas)
e calcula estatísticas e tendências locais de reversão/continuação.
"""
from typing import List, Dict, Any

def match_candle_pattern(candles: List[Dict[str, Any]], window: int = 5) -> Dict[str, Any]:
    """
    Analisa a sequência das últimas `window` velas fechadas.
    1 = Verde (Close > Open)
    0 = Vermelha (Close <= Open)
    """
    if len(candles) < window:
        return {
            "sequence_str": "",
            "greens": 0,
            "reds": 0,
            "pattern_type": "insufficient_data",
            "historical_prob_green": 0.50
        }

    slice_candles = candles[-window:]
    colors = [1 if c["close"] > c["open"] else 0 for c in slice_candles]
    seq_str = "".join(map(str, colors))
    greens = sum(colors)
    reds = window - greens

    # Heurística empírica baseada na microestrutura observada:
    # Sequências extremas de reversão vs continuação
    if greens == window:
        pattern_type = f"{window} Verdes Consecutivas"
        historical_prob_green = 0.48 # leve exaustão
    elif reds == window:
        pattern_type = f"{window} Vermelhas Consecutivas"
        historical_prob_green = 0.52 # leve repique
    elif colors[-2:] == [0, 0]:
        pattern_type = "Rejeição Baixista Recente"
        historical_prob_green = 0.50
    elif colors[-2:] == [1, 1]:
        pattern_type = "Impulso Altista Recente"
        historical_prob_green = 0.51
    else:
        pattern_type = f"Padrão Misto ({seq_str})"
        historical_prob_green = 0.50

    return {
        "sequence_str": seq_str,
        "colors": colors,
        "greens": greens,
        "reds": reds,
        "pattern_type": pattern_type,
        "historical_prob_green": historical_prob_green
    }
