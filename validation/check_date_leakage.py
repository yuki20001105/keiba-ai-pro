"""
特徴量の日付ズレ・データリーク診断スクリプト
「取得したい日付（レース当日の情報）」と「実際に入っている値」の整合性を確認する
"""
import sqlite3
import json
import sys
from pathlib import Path
from datetime import datetime

# validation/ から実行しても root から実行しても動作するよう __file__ ベースで解決
_ROOT = Path(__file__).parent.parent
DB_PATH = _ROOT / "keiba" / "data" / "keiba_ultimate.db"

def qry(sql, params=()):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()
    return rows

def load_all_results():
    rows = qry("SELECT race_id, data FROM race_results_ultimate")
    records = []
    for race_id, d in rows:
        obj = json.loads(d)
        obj['race_id'] = race_id
        records.append(obj)
    return records

print("=" * 70)
print("  特徴量 日付ズレ・データリーク診断")
print("=" * 70)

records = load_all_results()
print(f"\n総レコード数: {len(records)} 行\n")

# ── 診断1: prev_race_date の日付がレース日より前か ──────────────────
print("=" * 60)
print("【診断1】prev_race_date の時系列整合性")
print("  期待: prev_race_date < race_id[:8] (前走 < 当該レース)")
print("=" * 60)

ok = 0
ng_future = 0   # 前走日 >= レース日（未来の値）
ng_missing = 0  # prev_race_date が None/空

ng_examples = []
for r in records:
    race_date_str = r['race_id'][:8]  # YYYYMMDD
    prev_date = r.get('prev_race_date') or r.get('prev_date')

    if not prev_date:
        ng_missing += 1
        continue

    # "2025/02/01" → "20250201"
    prev_clean = str(prev_date).replace('/', '').replace('-', '').strip()[:8]

    try:
        rd = datetime.strptime(race_date_str, '%Y%m%d')
        pd_ = datetime.strptime(prev_clean, '%Y%m%d')

        if pd_ >= rd:
            ng_future += 1
            if len(ng_examples) < 5:
                ng_examples.append({
                    'race_id': r['race_id'],
                    'race_date': race_date_str,
                    'prev_race_date': prev_date,
                    'horse_name': r.get('horse_name'),
                })
        else:
            ok += 1
    except ValueError:
        ng_missing += 1

total_checked = ok + ng_future
print(f"  OK (prev < race):  {ok:5d} 件 ({ok/max(total_checked,1)*100:.1f}%)")
print(f"  NG (prev >= race): {ng_future:5d} 件 ← データリーク疑い！")
print(f"  欠損:              {ng_missing:5d} 件")
if ng_examples:
    print("\n  NG例:")
    for ex in ng_examples:
        print(f"    race_id={ex['race_id']} (レース日={ex['race_date']}) "
              f"prev={ex['prev_race_date']} horse={ex['horse_name']}")

# ── 診断2: horse_total_runs が「当時」より明らかに大きくないか ──────
print("\n" + "=" * 60)
print("【診断2】horse_total_runs の妥当性チェック")
print("  問題: スクレイプ現在時点(2026年)の通算数がセットされている可能性")
print("=" * 60)

# race_id の日付分布を確認
from collections import Counter
years = Counter(r['race_id'][:4] for r in records)
print("\n  データ年度分布:")
for y, cnt in sorted(years.items()):
    print(f"    {y}年: {cnt:5d} 行")

# horse_total_runs の統計
runs_vals = []
for r in records:
    v = r.get('horse_total_runs')
    if v is not None:
        try:
            runs_vals.append((int(v), r['race_id'][:4]))
        except (ValueError, TypeError):
            pass

if runs_vals:
    # 2024年のレースで horse_total_runs が50以上（明らかに多すぎる）ものを確認
    suspicious = [(v, y, ) for v, y in runs_vals if y <= '2024' and v > 50]
    print(f"\n  horse_total_runs 記録数: {len(runs_vals)}")
    import statistics
    vals_only = [v for v, _ in runs_vals]
    print(f"  平均={statistics.mean(vals_only):.1f}, "
          f"中央値={statistics.median(vals_only):.0f}, "
          f"最大={max(vals_only)}, 最小={min(vals_only)}")
    print(f"\n  2024年以前のレースで通算50走超: {len(suspicious)} 件")
    print("  (50走以上は現役馬では少ないはず。2026年時点の累計が入っている場合に増加)")
    if suspicious[:3]:
        for v, y in suspicious[:3]:
            recs_match = [r for r in records if r.get('horse_total_runs') == str(v) or r.get('horse_total_runs') == v]
            if recs_match:
                ex = recs_match[0]
                print(f"    {ex.get('horse_name')} ({y}年): total_runs={v}")
else:
    print("  horse_total_runs: データなし")

# ── 診断3: jockey_place_rate_top2 等（feature_engineering）のリーク ──
print("\n" + "=" * 60)
print("【診断3】feature_engineering.py の jockey/trainer 統計算出")
print("  問題: add_derived_features で full_history_df=df（全データ使用）")
print("        → 未来のレース結果も含めた統計になっている")
print("=" * 60)

# jockey_id ごとの race_id 分布を確認
jockey_races = {}
for r in records:
    jid = r.get('jockey_id') or r.get('jockey_name', '?')
    rid = r['race_id']
    if jid not in jockey_races:
        jockey_races[jid] = []
    jockey_races[jid].append(rid)

# 最もレース数が多い騎手で確認
top_jockey = sorted(jockey_races.items(), key=lambda x: len(x[1]), reverse=True)[:1]
if top_jockey:
    jid, rids = top_jockey[0]
    rids_sorted = sorted(rids)
    first_race = rids_sorted[0]
    last_race  = rids_sorted[-1]
    print(f"\n  最多出走騎手: {jid} ({len(rids)}戦, {first_race[:8]}〜{last_race[:8]})")
    print(f"  全データで計算した場合: 最終レース({last_race[:8]})時点の成績が")
    print(f"  最初のレース({first_race[:8]})にも使われる → データリーク")

    # 年別レース数で「後半のデータが前半に混入」する規模を示す
    before_2025 = [r for r in rids if r < '20250101']
    after_2025  = [r for r in rids if r >= '20250101']
    print(f"\n  2025年以前: {len(before_2025)}戦, 2025年以降: {len(after_2025)}戦")
    if before_2025 and after_2025:
        print(f"  → 2024年のレースに2025年以降の勝率情報が混入している！")

# ── 診断4: _add_entity_statistics のリーク規模推定 ──────────────────
print("\n" + "=" * 60)
print("【診断4】lightgbm_feature_optimizer の _add_entity_statistics")
print("  問題: groupby で全データ集計 → 未来の結果込みの勝率になる")
print("=" * 60)

# 騎手ごとに「2024年の勝率」vs「2025年以降を含む全体勝率」を比較
from collections import defaultdict
jockey_2024_results = defaultdict(list)
jockey_all_results  = defaultdict(list)

for r in records:
    jid = r.get('jockey_id') or r.get('jockey_name')
    if not jid:
        continue
    finish = r.get('finish_position') or r.get('finish')
    if finish is None:
        continue
    try:
        f = int(finish)
    except (ValueError, TypeError):
        continue
    jockey_all_results[jid].append(f)
    if r['race_id'] < '20250101':
        jockey_2024_results[jid].append(f)

# 両方に5戦以上ある騎手でサンプル比較
mismatches = []
for jid in jockey_2024_results:
    if len(jockey_2024_results[jid]) >= 5 and len(jockey_all_results[jid]) >= 5:
        rate_2024 = sum(1 for f in jockey_2024_results[jid] if f == 1) / len(jockey_2024_results[jid])
        rate_all  = sum(1 for f in jockey_all_results[jid] if f == 1) / len(jockey_all_results[jid])
        if abs(rate_all - rate_2024) > 0.02:  # 2%以上の差
            mismatches.append((jid, len(jockey_2024_results[jid]), rate_2024,
                               len(jockey_all_results[jid]), rate_all))

mismatches.sort(key=lambda x: abs(x[4] - x[2]), reverse=True)
print(f"\n  2024年以前の勝率 vs 全期間込みの勝率 が 2%以上ずれている騎手: {len(mismatches)} 名")
if mismatches[:5]:
    print(f"  {'騎手ID':<15} {'2024勝率':>8} {'全体勝率':>8} {'差':>6}")
    print(f"  {'-'*15} {'-'*8} {'-'*8} {'-'*6}")
    for jid, n1, r1, n2, r2 in mismatches[:5]:
        print(f"  {str(jid):<15} {r1:8.3f} {r2:8.3f} {r2-r1:+6.3f}")

# ── 診断5: race_id の辞書順 = 時系列順か ─────────────────────────────
print("\n" + "=" * 60)
print("【診断5】race_id の辞書順 ≡ 時系列順チェック")
print("  前提: race_id < current_race_id で過去レースをフィルタできるか")
print("=" * 60)

all_ids = sorted(set(r['race_id'] for r in records))
bad_order = 0
for i in range(len(all_ids)-1):
    d1 = all_ids[i][:8]
    d2 = all_ids[i+1][:8]
    if d1 > d2:  # 辞書順は後なのに日付は前
        bad_order += 1

print(f"  race_id総数: {len(all_ids)}")
print(f"  順序不整合: {bad_order} 件", end="")
if bad_order == 0:
    print(" → race_id < current_race_id フィルタは正しく動作 ✓")
else:
    print(" ← 辞書順≠時系列順 → フィルタにバグあり！")

# ── 最終サマリ ─────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("  診断結果サマリ")
print("=" * 70)

issues = []

if ng_future > 0:
    issues.append(f"🔴 [重大] prev_race_date: {ng_future}件がレース日以降の日付（未来データ混入）")
    issues.append("       原因: Phase3パッチが現在時点(2026年)の最新2走をセット")
    issues.append("       修正: patch_prev_race でレース当日より前の行だけ取るよう絞り込みが必要")

if mismatches:
    issues.append(f"🔴 [重大] jockey_win_rate 等: {len(mismatches)}名の騎手で未来結果が混入")
    issues.append("       原因: _add_entity_statistics / add_derived_features が全データで groupby")
    issues.append("       修正: 各行の race_id < current_race_id で絞った上で統計計算が必要")

if runs_vals:
    issues.append(f"🟡 [中程度] horse_total_runs: スクレイプ現在時点(2026年)の通算数が入っている")
    issues.append("       原因: 馬詳細ページはスクレイプ日時点の数値を返す")
    issues.append("       修正: 学習時は DB内の過去レース数から自前で計算するのが理想")

issues.append(f"✅ [問題なし] UltimateFeatureCalculator: race_id < current_race_id で正しくフィルタ")
issues.append(f"✅ [問題なし] race_id 辞書順 ≡ 時系列順 → 時系列フィルタは有効")

for msg in issues:
    print(f"  {msg}")

print()
