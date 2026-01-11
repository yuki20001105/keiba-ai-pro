from __future__ import annotations
import argparse
from pathlib import Path
from typing import Optional, Sequence

import joblib
import numpy as np
import pandas as pd

from .config import load_config
from .netkeiba.client import NetkeibaClient, NetkeibaBlockedError
from .netkeiba.parsers import parse_shutuba_table

def predict_race(cfg_path: Path, model_path: Path, race_id: str, topk: int = 5) -> pd.DataFrame:
    cfg = load_config(cfg_path)
    bundle = joblib.load(model_path)
    model = bundle["model"]
    num_cols = bundle["feature_cols_num"]
    cat_cols = bundle["feature_cols_cat"]

    client = NetkeibaClient(cfg.netkeiba, cfg.storage)
    url = client.build_url(cfg.netkeiba.shutuba_url.format(race_id=race_id))
    fr = client.fetch_html(url, cache_kind="shutuba", cache_key=race_id, use_cache=False)
    df = parse_shutuba_table(fr.text)

    # ensure columns
    for c in num_cols + cat_cols:
        if c not in df.columns:
            df[c] = np.nan

    X = df[num_cols + cat_cols].copy()
    proba = model.predict_proba(X)[:, 1]
    out = df.copy()
    out["p_win_like"] = proba
    out = out.sort_values("p_win_like", ascending=False)

    cols_show = []
    for c in ["bracket","horse_no","horse_name","sex","age","handicap","jockey_name","trainer_name","odds","popularity","p_win_like"]:
        if c in out.columns:
            cols_show.append(c)

    return out[cols_show].head(topk)

def main(argv: Optional[Sequence[str]] = None) -> None:
    p = argparse.ArgumentParser(description="Predict for a race_id using a trained model.")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--model", required=True)
    p.add_argument("--race_id", required=True)
    p.add_argument("--topk", type=int, default=5)
    args = p.parse_args(argv)

    df = predict_race(Path(args.config), Path(args.model), args.race_id, topk=args.topk)
    print(df.to_string(index=False))

if __name__ == "__main__":
    main()
