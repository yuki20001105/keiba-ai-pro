from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Optional, List, Dict, Tuple
import pandas as pd
from bs4 import BeautifulSoup

RACE_ID_RE = re.compile(r"race_id=(\d{12})")
HORSE_ID_RE = re.compile(r"/horse/(\d+)")
JOCKEY_ID_RE = re.compile(r"/jockey/(?:result/recent/)?(\d+)")
TRAINER_ID_RE = re.compile(r"/trainer/(?:result/recent/)?(\d+)")

_PATTERNS = [
    re.compile(r"race_id=(\d{12})"),
    re.compile(r"/race/(?:shutuba|result)\.html\?race_id=(\d{12})"),
    re.compile(r"data-race-id=['\"](\d{12})['\"]"),
    re.compile(r'"race_id"\s*:\s*"(\d{12})"'),
]

def extract_race_ids(html: str) -> list[str]:
    ids: set[str] = set()
    for pat in _PATTERNS:
        hits = pat.findall(html)
        for h in hits:
            # findall が tuple を返すケースに備える
            if isinstance(h, tuple):
                h = h[0]
            if h:
                ids.add(h)
    return sorted(ids)

def _text(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()

def _to_int(x) -> Optional[int]:
    try:
        if pd.isna(x):
            return None
        s = str(x).strip()
        if s == "" or s == "-":
            return None
        return int(float(s))
    except Exception:
        return None

def _to_float(x) -> Optional[float]:
    try:
        if pd.isna(x):
            return None
        s = str(x).strip()
        if s == "" or s == "-":
            return None
        # odds sometimes like '12.3'
        return float(s)
    except Exception:
        return None

def _parse_sex_age(sex_age: str) -> tuple[Optional[str], Optional[int]]:
    s = str(sex_age).strip()
    if not s:
        return None, None
    # e.g. 牡5, 牝3, セ4
    sex = s[0]
    age = _to_int(s[1:]) if len(s) > 1 else None
    return sex, age

def _parse_weight(weight_str: str) -> tuple[Optional[int], Optional[int]]:
    # e.g. 514(+6) or 480(-2) or 514<small>(+6)</small> or 458<small></small> or --- (before race)
    s = str(weight_str).strip()
    if not s or s == "---" or s == "計不":
        return None, None
    # Remove HTML tags like <small></small>
    s = re.sub(r'<[^>]+>', '', s)
    s = s.strip()
    # Match: "514(+6)" or "514(-2)" or "514(0)" or just "514"
    m = re.match(r"(\d+)(?:\(([+-]?\d+)\))?", s)
    if not m:
        return None, None
    w = _to_int(m.group(1))
    # If there's a diff part, parse it (including 0)
    dw = None
    if m.group(2):
        dw_val = _to_int(m.group(2))
        # Only store non-zero differences (0 means no change, effectively None)
        dw = dw_val if dw_val != 0 else 0
    return w, dw

def parse_shutuba_table(html: str) -> pd.DataFrame:
    """Parse entry (出馬表) page into a dataframe.
    This returns only a baseline set of columns; you can extend as needed.
    """
    from io import StringIO
    
    soup = BeautifulSoup(html, "lxml")

    # Table usually readable by pandas
    tables = pd.read_html(StringIO(html))
    if not tables:
        raise ValueError("No tables found in shutuba page.")
    df = tables[0].copy()
    
    # マルチインデックスカラムを単一レベルに変換
    if isinstance(df.columns, pd.MultiIndex):
        # タプルのカラム名を文字列に変換（最初の要素を使用）
        df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]

    # Add IDs from links (horse/jockey/trainer)
    # We align by row order in the main table.
    table = soup.find("table")
    if table:
        rows = table.find_all("tr")[1:]  # skip header
        horse_ids, jockey_ids, trainer_ids = [], [], []
        for tr in rows[: len(df)]:
            a_horse = tr.find("a", href=HORSE_ID_RE)
            a_jockey = tr.find("a", href=JOCKEY_ID_RE)
            a_trainer = tr.find("a", href=TRAINER_ID_RE)
            horse_ids.append(HORSE_ID_RE.search(a_horse["href"]).group(1) if a_horse and HORSE_ID_RE.search(a_horse.get("href","")) else None)
            jockey_ids.append(JOCKEY_ID_RE.search(a_jockey["href"]).group(1) if a_jockey and JOCKEY_ID_RE.search(a_jockey.get("href","")) else None)
            trainer_ids.append(TRAINER_ID_RE.search(a_trainer["href"]).group(1) if a_trainer and TRAINER_ID_RE.search(a_trainer.get("href","")) else None)

        df["horse_id"] = horse_ids + [None] * max(0, len(df) - len(horse_ids))
        df["jockey_id"] = jockey_ids + [None] * max(0, len(df) - len(jockey_ids))
        df["trainer_id"] = trainer_ids + [None] * max(0, len(df) - len(trainer_ids))

    # Normalize common columns (best-effort; column names vary)
    # We'll try to map by partial matches.
    col_map = {}
    for c in df.columns:
        cs = str(c).replace(" ", "")  # スペースを除去して比較
        if "馬番" in cs or cs == "馬番":
            col_map[c] = "horse_no"
        elif cs in ("馬名", "Horse"):
            col_map[c] = "horse_name"
        elif "性齢" in cs or "Sex/Age" in cs:
            col_map[c] = "sex_age"
        elif "斤量" in cs or "Wgt" in cs:
            col_map[c] = "handicap"
        elif "騎手" in cs or "Jockey" in cs:
            col_map[c] = "jockey_name"
        elif "調教師" in cs or "Trainer" in cs:
            col_map[c] = "trainer_name"
        elif "馬体重" in cs or "Body Wt" in cs:
            col_map[c] = "body_weight"
        elif "オッズ" in cs or "Odds" in cs:
            col_map[c] = "odds"
        elif "人気" in cs or "Fav" in cs:
            col_map[c] = "popularity"
        elif "枠" in cs or "枠番" in cs:
            col_map[c] = "bracket"

    df = df.rename(columns=col_map)

    if "sex_age" in df.columns:
        sex_age_parsed = df["sex_age"].apply(_parse_sex_age)
        df["sex"] = sex_age_parsed.apply(lambda t: t[0])
        df["age"] = sex_age_parsed.apply(lambda t: t[1])

    if "body_weight" in df.columns:
        w = df["body_weight"].apply(_parse_weight)
        df["weight"] = w.apply(lambda t: t[0])
        df["weight_diff"] = w.apply(lambda t: t[1])

    # numeric casts
    for c in ("horse_no", "bracket", "age", "weight", "weight_diff", "popularity"):
        if c in df.columns:
            df[c] = df[c].apply(_to_int)
    if "odds" in df.columns:
        df["odds"] = df["odds"].apply(_to_float)
    if "handicap" in df.columns:
        df["handicap"] = df["handicap"].apply(_to_float)

    return df

def parse_result_table(html: str) -> pd.DataFrame:
    """Parse result (結果) page into a dataframe."""
    soup = BeautifulSoup(html, "lxml")
    
    # BeautifulSoupで直接テーブルをパース（エンコーディング問題回避）
    table = soup.find("table", class_="race_table_01 nk_tb_common")
    if not table:
        table = soup.find("table")
    if not table:
        raise ValueError("No tables found in result page.")
    
    # ヘッダー行を取得
    header_row = table.find("tr")
    if not header_row:
        raise ValueError("No header row found")
    
    headers = []
    for th in header_row.find_all(["th", "td"]):
        headers.append(_text(th.get_text()))
    
    # データ行を取得
    data_rows = []
    rows = table.find_all("tr")[1:]  # ヘッダーをスキップ
    
    horse_ids, jockey_ids, trainer_ids = [], [], []
    for tr in rows:
        cells = tr.find_all("td")
        if not cells:
            continue
        
        row_data = [_text(cell.get_text()) for cell in cells]
        data_rows.append(row_data)
        
        # IDを抽出
        a_horse = tr.find("a", href=HORSE_ID_RE)
        a_jockey = tr.find("a", href=JOCKEY_ID_RE)
        a_trainer = tr.find("a", href=TRAINER_ID_RE)
        
        horse_ids.append(HORSE_ID_RE.search(a_horse["href"]).group(1) if a_horse and HORSE_ID_RE.search(a_horse.get("href","")) else None)
        jockey_ids.append(JOCKEY_ID_RE.search(a_jockey["href"]).group(1) if a_jockey and JOCKEY_ID_RE.search(a_jockey.get("href","")) else None)
        trainer_ids.append(TRAINER_ID_RE.search(a_trainer["href"]).group(1) if a_trainer and TRAINER_ID_RE.search(a_trainer.get("href","")) else None)
    
    # DataFrameを作成
    df = pd.DataFrame(data_rows, columns=headers[:len(data_rows[0])] if data_rows else [])
    
    df["horse_id"] = horse_ids
    df["jockey_id"] = jockey_ids
    df["trainer_id"] = trainer_ids

    # Normalize columns (best-effort)
    col_map = {}
    for c in df.columns:
        cs = str(c)
        if cs in ("着順", "Place", "FP") or "着" in cs and "順" in cs:
            col_map[c] = "finish"
        elif "馬番" in cs or "馬" in cs and "番" in cs:
            col_map[c] = "horse_no"
        elif cs in ("馬名", "Horse") or "馬" in cs and "名" in cs:
            col_map[c] = "horse_name"
        elif "性齢" in cs or "Sex/Age" in cs or ("性" in cs or "齢" in cs):
            col_map[c] = "sex_age"
        elif "斤量" in cs or "Wgt" in cs or "斤" in cs:
            col_map[c] = "handicap"
        elif "騎手" in cs or "Jockey" in cs or "騎" in cs:
            col_map[c] = "jockey_name"
        elif "調教師" in cs or "Trainer" in cs:
            col_map[c] = "trainer_name"
        elif "タイム" in cs or "Time" in cs:
            col_map[c] = "time"
        elif "着差" in cs or "Mrg" in cs:
            col_map[c] = "margin"
        elif "上り" in cs or "3F" in cs:
            col_map[c] = "last3f"
        elif "通過" in cs or "Pass" in cs:
            col_map[c] = "pass_order"
        elif "人気" in cs or "Fav" in cs:
            col_map[c] = "popularity"
        elif "オッズ" in cs or "Odds" in cs:
            col_map[c] = "odds"
        elif "馬体重" in cs or "Body Wt" in cs:
            col_map[c] = "body_weight"
        elif "枠" in cs or "枠番" in cs or "BK" in cs:
            col_map[c] = "bracket"

    df = df.rename(columns=col_map)

    if "sex_age" in df.columns:
        sex_age_parsed = df["sex_age"].apply(_parse_sex_age)
        df["sex"] = sex_age_parsed.apply(lambda t: t[0])
        df["age"] = sex_age_parsed.apply(lambda t: t[1])

    if "body_weight" in df.columns:
        w = df["body_weight"].apply(_parse_weight)
        df["weight"] = w.apply(lambda t: t[0])
        df["weight_diff"] = w.apply(lambda t: t[1])

    for c in ("finish", "horse_no", "bracket", "age", "weight", "weight_diff", "popularity"):
        if c in df.columns:
            df[c] = df[c].apply(_to_int)
    for c in ("odds", "handicap", "last3f"):
        if c in df.columns:
            df[c] = df[c].apply(_to_float)

    return df


def extract_today_race_ids_from_top(html: str) -> list[str]:
    """
    netkeibaトップページから今日/直近のレースIDを抽出
    https://race.netkeiba.com/top/ から取得可能
    """
    soup = BeautifulSoup(html, "lxml")
    race_ids = set()
    
    # パターン1: race_idパラメータを含むリンクを探す
    for link in soup.find_all('a', href=True):
        href = link['href']
        match = RACE_ID_RE.search(href)
        if match:
            race_ids.add(match.group(1))
    
    # パターン2: data-race-id属性
    for elem in soup.find_all(attrs={'data-race-id': True}):
        rid = elem.get('data-race-id')
        if rid and len(rid) == 12 and rid.isdigit():
            race_ids.add(rid)
    
    # パターン3: JavaScriptコード内のrace_id
    for script in soup.find_all('script'):
        if script.string:
            matches = re.findall(r'"race_id"\s*:\s*"(\d{12})"', script.string)
            race_ids.update(matches)
            matches = re.findall(r'race_id=(\d{12})', script.string)
            race_ids.update(matches)
    
    return sorted(race_ids)


def extract_race_calendar(html: str) -> dict[str, list[str]]:
    """
    netkeibaの開催カレンダーから日付別のレースIDを抽出
    Returns: {kaisai_date: [race_id, ...]}
    """
    soup = BeautifulSoup(html, "lxml")
    calendar = {}
    
    # カレンダー形式のテーブルやリストを探索
    # 開催日ごとにグルーピングされている可能性がある
    
    # パターン1: 日付情報を含む要素とレースIDをマッピング
    date_pattern = re.compile(r'(\d{8})')  # YYYYMMDD
    
    # すべてのリンクを走査
    for link in soup.find_all('a', href=True):
        href = link['href']
        
        # レースIDを抽出
        race_match = RACE_ID_RE.search(href)
        if race_match:
            race_id = race_match.group(1)
            # レースIDの最初の8桁が日付
            kaisai_date = race_id[:8]
            
            if kaisai_date not in calendar:
                calendar[kaisai_date] = []
            calendar[kaisai_date].append(race_id)
    
    # 重複削除とソート
    for date in calendar:
        calendar[date] = sorted(set(calendar[date]))
    
    return calendar
