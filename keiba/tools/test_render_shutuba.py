from io import StringIO
from pathlib import Path
import pandas as pd
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

RID = "2025XXXXXXXXXX"  # 実値に置換
URL = f"https://race.netkeiba.com/race/shutuba.html?race_id={RID}&rf=race_submenu"

OUT = Path(f"./data/html_rendered/shutuba/{RID}.html")
OUT.parent.mkdir(parents=True, exist_ok=True)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"

def flatten_cols(df: pd.DataFrame) -> pd.DataFrame:
    # MultiIndex列を平坦化（あなたの出力みたいに (枠,枠) 形式になる対策）
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [" ".join([str(x) for x in tup if str(x) != "nan"]).strip() for tup in df.columns.values]
    else:
        df.columns = [str(c) for c in df.columns]
    return df

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent=UA,
        locale="ja-JP",
        extra_http_headers={"Accept-Language": "ja,en-US;q=0.7,en;q=0.3"},
    )
    page = context.new_page()

    # 重いリソースを落としてタイムアウトしにくくする
    page.route("**/*", lambda route, request:
               route.abort() if request.resource_type in ("image", "media", "font") else route.continue_())

    page.set_default_navigation_timeout(120_000)
    page.set_default_timeout(120_000)

    print("[pw] goto:", URL)
    resp = page.goto(URL, wait_until="domcontentloaded")  # ★ networkidle は使わない
    print("[pw] status:", resp.status if resp else None, "final_url:", page.url)

    # 出馬表テーブルが出るまで待つ（候補を複数）
    selectors = [
        "table",                 # 最低限
        ".Shutuba_Table",        # 旧/別クラスの保険
        ".ShutubaTable",         # 保険
    ]
    last_err = None
    for sel in selectors:
        try:
            page.wait_for_selector(sel, timeout=60_000)
            print("[pw] selector ok:", sel)
            break
        except Exception as e:
            last_err = e
    else:
        # どれも出なければHTML保存して中身確認
        html = page.content()
        OUT.write_text(html, encoding="utf-8", errors="replace")
        browser.close()
        raise RuntimeError(f"Table not found. Saved: {OUT}. last_err={last_err}")

    # 少し待ってDOMが安定するのを待つ（ネット環境次第）
    page.wait_for_timeout(1500)

    html = page.content()
    OUT.write_text(html, encoding="utf-8", errors="replace")
    browser.close()

print("[pw] saved:", OUT, "size:", OUT.stat().st_size)

# pandasでテーブル抽出
dfs = pd.read_html(StringIO(html))
print("tables:", len(dfs))
df0 = flatten_cols(dfs[0])
print("shape0:", df0.shape)
print(df0.head(5))
