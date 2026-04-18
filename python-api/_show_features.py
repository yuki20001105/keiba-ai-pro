import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from app_config import load_model_bundle

path = os.path.join(os.path.dirname(__file__), "models", "model_speed_deviation_lightgbm_20160101_20260322_ultimate.joblib")
bundle = load_model_bundle(path)
model = bundle.get("model") or bundle
names = model.feature_name()
print(f"Total features: {len(names)}")
print()

# カテゴリ別に分類して表示
categories = {
    "前走・前々走 着順/タイム": [n for n in names if "prev" in n and ("finish" in n or "time" in n or "speed" in n or "distance" in n or "weight" in n)],
    "通算・近走 成績": [n for n in names if "horse_win" in n or "horse_total" in n or "past" in n],
    "条件別適性": [n for n in names if "horse_distance" in n or "horse_surface" in n or "horse_venue" in n or "horse_dist" in n],
    "騎手・調教師": [n for n in names if "jockey" in n or "trainer" in n or "jt_combo" in n],
    "オッズ関連": [n for n in names if "odds" in n or "implied" in n or "market" in n or "top3" in n or "popular" in n],
    "レース条件": [n for n in names if n in ("distance","race_class_encoded","venue_encoded","venue_code_encoded","frame_race_type","field_condition_encoded","num_horses","race_num","kai","day","surface","track_type")],
    "日付周期": [n for n in names if "sin_" in n or "cos_" in n or "season" in n],
    "脚質・ペース": [n for n in names if "running_style" in n or "pace" in n],
    "馬体重・基本": [n for n in names if n in ("horse_weight","horse_weight_change","age","bracket_number","horse_number","burden_weight","gate_win_rate")],
    "血統": [n for n in names if "sire" in n or "damsire" in n],
    "欠損フラグ": [n for n in names if n.endswith("_is_missing")],
}

already = set()
for cat, cols in categories.items():
    cols = [c for c in cols if c not in already]
    if cols:
        print(f"[{cat}]  ({len(cols)}件)")
        for c in cols:
            print(f"  {c}")
        already.update(cols)
        print()

rest = [n for n in names if n not in already]
if rest:
    print(f"[その他]  ({len(rest)}件)")
    for c in rest:
        print(f"  {c}")
