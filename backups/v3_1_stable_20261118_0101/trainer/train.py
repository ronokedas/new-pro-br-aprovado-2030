"""
train.py
Treina o modelo HistGradientBoosting com base nas features de 1m e salva em models/gboost_latest.joblib
"""
import sys
from pathlib import Path
import logging
import joblib
import pandas as pd
import numpy as np
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.ensemble import HistGradientBoostingClassifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("train")

BASE_DIR = Path(__file__).resolve().parent.parent
MODELS_DIR = BASE_DIR / "models"
MODELS_DIR.mkdir(exist_ok=True)

RESEARCH_FEATURES = Path("c:/trading-agents/btc_color_research/results/features_1m.parquet")

def train_and_save():
    if not RESEARCH_FEATURES.exists():
        log.error("Arquivo de features não encontrado: %s", RESEARCH_FEATURES)
        return False

    log.info("Carregando features de %s...", RESEARCH_FEATURES)
    df = pd.read_parquet(RESEARCH_FEATURES)
    
    # Feature columns essenciais de alta velocidade
    feature_cols = [
        "ema9_21_cross", "ema50_150_cross", "price_vs_ema50", "price_vs_ema200",
        "rsi14", "rsi7", "rsi_diff", "macd_line", "macd_signal", "macd_hist",
        "cci20", "cci50", "vol_zscore_20", "vol_zscore_60", "vol_ma_ratio",
        "cvd5_norm", "cvd30_norm", "range_pos60", "ret1", "ret3", "ret5",
        "vol_real_10", "vol_real_60", "hl_range", "body_pct"
    ]
    feature_cols = [c for c in feature_cols if c in df.columns]
    
    df_clean = df.dropna(subset=feature_cols + ["target"]).copy()
    log.info("Total amostras limpas: %d, features: %d", len(df_clean), len(feature_cols))
    
    # Treina com as últimas 20.000 velas para capturar o regime mais recente
    train_df = df_clean.iloc[-20000:] if len(df_clean) > 20000 else df_clean
    X = train_df[feature_cols].values
    y = train_df["target"].astype(int).values
    
    pipeline = Pipeline([
        ("scaler", StandardScaler()),
        ("clf", HistGradientBoostingClassifier(
            max_iter=100,
            max_depth=4,
            learning_rate=0.04,
            min_samples_leaf=40,
            random_state=42
        ))
    ])
    
    log.info("Treinando HistGradientBoostingClassifier...")
    pipeline.fit(X, y)
    
    model_payload = {
        "pipeline": pipeline,
        "feature_cols": feature_cols,
        "trained_samples": len(train_df),
        "target_col": "target"
    }
    
    out_file = MODELS_DIR / "gboost_latest.joblib"
    joblib.dump(model_payload, out_file)
    log.info("Modelo salvo com sucesso em: %s", out_file)
    return True

if __name__ == "__main__":
    train_and_save()
