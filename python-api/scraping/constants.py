"""
スクレイピング共通定数
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import re
from pathlib import Path

from bs4 import SoupStrainer

# ── .env 読み込み（ローカル開発用 / 本番は環境変数優先） ──────────
# load_dotenv は .env が混合エンコードだと失敗するため手動パース
def _load_env_safe(env_path: Path) -> None:
    """encoding-safe な .env 読み込み。ASCII行のみ処理（コメント・日本語行はスキップ）。"""
    if not env_path.exists():
        return
    try:
        raw = env_path.read_bytes()
        for line_bytes in raw.split(b"\n"):
            try:
                line = line_bytes.decode("ascii").strip()
            except UnicodeDecodeError:
                continue  # 日本語コメント等はスキップ
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip()
                if k and k not in os.environ:
                    os.environ[k] = v
    except Exception:
        pass

_load_env_safe(Path(__file__).parent.parent.parent / ".env")

_logger = logging.getLogger(__name__)

# ── プロキシ設定（環境変数 SCRAPE_PROXY_URL で指定） ─────────────────
#   例: SCRAPE_PROXY_URL=http://user:pass@proxy.example.com:8080
SCRAPE_PROXY_URL: str | None = os.environ.get("SCRAPE_PROXY_URL") or None

# ── netkeiba プレミアム会員認証（調教タイム取得に必要） ────────────────
#   .env に NETKEIBA_EMAIL / NETKEIBA_PASSWORD を設定してください。
NETKEIBA_EMAIL: str | None = os.environ.get("NETKEIBA_EMAIL") or None
NETKEIBA_PASSWORD: str | None = os.environ.get("NETKEIBA_PASSWORD") or None

# ── Cloudflare ブロック検知 ────────────────────────────────────────
#   netkeiba が CF Bot-Management を返す場合、39 バイト前後の極小 HTML になる
_CF_BLOCK_MAX_BYTES = 512


class IPBlockedError(Exception):
    """netkeiba から HTTP 400 が返された場合（IPブロック）に raise する。"""


# ── ジッター付きスリープ（固定間隔によるロボット検知回避） ─────────────────
async def jitter_sleep(min_s: float = 1.5, max_s: float = 3.5) -> None:
    """ランダムな待機時間でスリープ。INV-07 最低 1.0 秒を保証。"""
    await asyncio.sleep(max(1.0, min_s) + random.random() * max(0.0, max_s - min_s))


async def warm_up_netkeiba(session) -> None:
    """netkeiba トップページを訪問して Cookie を取得する。

    人間らしいナビゲーション（ホームページ → レースページ）をシミュレートし、
    IP ブロックリスクを低減する。
    """
    _kwargs: dict = {}
    if SCRAPE_PROXY_URL:
        _kwargs["proxy"] = SCRAPE_PROXY_URL
    try:
        async with session.get("https://db.netkeiba.com/", **_kwargs) as resp:
            await resp.read()
            _logger.debug(f"[warm-up] db.netkeiba.com → HTTP {resp.status}")
    except Exception as e:
        _logger.debug(f"[warm-up] トップページ取得失敗（無視）: {e}")
    await jitter_sleep(1.0, 2.5)


def is_cloudflare_block(content: bytes) -> bool:
    """レスポンスが Cloudflare/ボット判定ブロックか確認する。"""
    if len(content) < _CF_BLOCK_MAX_BYTES:
        text = content.decode("utf-8", errors="ignore").lower()
        return "cloudflare" in text or "cf-ray" in text or len(content) < 100
    return False

# ── HTMLパース最適化: 不要タグを除外してメモリ削減（60〜70%削減） ──────
HTML_STRAINER = SoupStrainer(
    [
        "html", "body",
        "table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption", "col", "colgroup",
        "div", "span", "p", "a", "b", "i", "em", "strong", "small", "br", "hr",
        "h1", "h2", "h3", "h4", "h5", "h6",
        "dl", "dt", "dd", "ul", "ol", "li",
        "form", "input", "select", "option", "img",
        "section", "article", "aside", "header", "footer", "main", "nav",
    ]
)

# ── 競馬場コードマップ（JRA + NAR） ─────────────────────────────────
VENUE_MAP: dict[str, str] = {
    # JRA
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟",
    "05": "東京", "06": "中山", "07": "中京", "08": "京都",
    "09": "阪神", "10": "小倉",
    # NAR
    "30": "門別", "31": "帯広（ば）",
    "35": "盛岡", "36": "水沢",
    "42": "浦和", "43": "船橋", "44": "大井", "45": "川崎",
    "46": "金沢", "47": "笠松", "48": "名古屋",
    "50": "園田", "51": "姫路",
    "54": "福山", "55": "高知",
    "60": "佐賀",
    "65": "帯広(ばんえい)", "66": "中津",
}

# ── HTTP リクエストヘッダー ────────────────────────────────────────
# 2024/11以降 netkeiba は固定UAを検知してブロックするため、ランダムローテーションが必須
USER_AGENTS: list[str] = [
    # Chrome on Windows (2025)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    # Chrome on macOS (2025)
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 15_3_2) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    # Firefox on Windows (2025)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:137.0) Gecko/20100101 Firefox/137.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:136.0) Gecko/20100101 Firefox/136.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:135.0) Gecko/20100101 Firefox/135.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:134.0) Gecko/20100101 Firefox/134.0",
    # Firefox on macOS (2025)
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:137.0) Gecko/20100101 Firefox/137.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:136.0) Gecko/20100101 Firefox/136.0",
    # Safari on macOS (2025)
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 15_3_2) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3.1 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 15_2) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
    # Edge on Windows (2025)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36 Edg/135.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36 Edg/133.0.0.0",
    # Chrome on Linux (2025)
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
]

# Referer のバリエーション（人間らしいナビゲーション経路を再現）
_REFERERS: list[str] = [
    "https://db.netkeiba.com/",
    "https://db.netkeiba.com/race/list/",
    "https://race.netkeiba.com/top/",
    "https://race.netkeiba.com/race/list.html",
    "https://www.google.co.jp/",
    "https://www.google.com/",
]


def get_random_headers(referer: str | None = None) -> dict[str, str]:
    """リクエストごとにランダムなUser-Agentと自然なヘッダーを返す。

    sec-ch-ua / Sec-Fetch-* ヘッダーを含めることで、アプリレベルの bot 検出を回避する。
    referer を指定しない場合は _REFERERS からランダムに選択する。
    """
    ua = random.choice(USER_AGENTS)
    _ref = referer if referer is not None else random.choice(_REFERERS)
    # Accept-Language を揺らす（完全固定だと bot 検出されやすい）
    _accept_lang = random.choice([
        "ja,en-US;q=0.9,en;q=0.8",
        "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
        "ja,en;q=0.9",
    ])
    # Sec-Fetch-Site: Google 参照元は cross-site、netkeiba 内は same-site
    _sec_fetch_site = "cross-site" if "google" in _ref else "same-site"
    headers: dict[str, str] = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": _accept_lang,
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Referer": _ref,
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": _sec_fetch_site,
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }
    # Chrome / Edge UA の場合は sec-ch-ua Client Hints も付与する
    chrome_ver_match = re.search(r"Chrome/(\d+)", ua)
    if chrome_ver_match:
        v = chrome_ver_match.group(1)
        if "Edg/" in ua:
            edge_ver_match = re.search(r"Edg/(\d+)", ua)
            ev = edge_ver_match.group(1) if edge_ver_match else v
            headers["sec-ch-ua"] = (
                f'"Microsoft Edge";v="{ev}", "Chromium";v="{v}", "Not_A Brand";v="8"'
            )
        else:
            headers["sec-ch-ua"] = (
                f'"Google Chrome";v="{v}", "Chromium";v="{v}", "Not_A Brand";v="8"'
            )
        headers["sec-ch-ua-mobile"] = "?0"
        # macOS UA かどうかで platform を切り替える
        if "Macintosh" in ua:
            headers["sec-ch-ua-platform"] = '"macOS"'
        elif "Linux" in ua:
            headers["sec-ch-ua-platform"] = '"Linux"'
        else:
            headers["sec-ch-ua-platform"] = '"Windows"'
    return headers


# 後方互換性のために固定ヘッダーも残す（セッション初期化などで使用）
SCRAPE_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Referer": "https://db.netkeiba.com/",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

# ── 毛色定数（長いパターンを先に: 部分マッチ防止） ────────────────────
COAT_COLORS: list[str] = [
    "黒鹿毛", "青鹿駁毛", "青鹿毛", "青駁毛", "鹿駁毛", "栗駁毛", "駁栗毛",
    "駁鹿毛", "駁青毛", "駁青鹿毛", "駁青暴毛", "栃栗毛",
    "鹿毛", "青毛", "栗毛", "芦毛", "白毛", "駁毛",
]
COAT_RE = re.compile("|".join(re.escape(c) for c in COAT_COLORS))


# ── netkeiba ログイン ────────────────────────────────────────────────────────
_NETKEIBA_LOGIN_URL = "https://regist.netkeiba.com/account/?pid=login"
_netkeiba_logged_in: bool = False  # セッションごとのログイン済みフラグ


async def login_netkeiba(session) -> bool:
    """
    netkeiba.com にプレミアム会員としてログインする。
    セッションのCookieJarにクッキーが保存され、以降のリクエストで自動送信される。
    環境変数 NETKEIBA_EMAIL / NETKEIBA_PASSWORD が未設定の場合は何もしない。
    """
    global _netkeiba_logged_in
    if not NETKEIBA_EMAIL or not NETKEIBA_PASSWORD:
        _logger.debug("NETKEIBA_EMAIL/NETKEIBA_PASSWORD 未設定 → 調教タイム取得スキップ")
        return False

    try:
        # ステップ1: ログインページを取得してフォームフィールドを確認
        async with session.get(_NETKEIBA_LOGIN_URL) as get_resp:
            if get_resp.status != 200:
                _logger.warning(f"netkeiba ログインページ取得失敗: HTTP {get_resp.status}")
                return False
            login_html = (await get_resp.read()).decode("euc-jp", errors="replace")

        # フォームフィールド名を全て抽出してログに出力（デバッグ用）
        _form_inputs = re.findall(
            r'<input[^>]+name=["\']?([^"\'>\s]+)["\']?',
            login_html, re.IGNORECASE
        )
        _logger.debug(f"login form input names: {_form_inputs}")

        # CSRFトークンがあれば抽出（なければそのままPOST）
        csrf_m = re.search(
            r'<input[^>]+name=["\']?(?:csrf|_token|xsrf)["\']?[^>]+value=["\']([^"\']+)["\']',
            login_html, re.IGNORECASE
        )
        csrf_token = csrf_m.group(1) if csrf_m else None

        # hidden フィールドを全て収集（action=auth 等が必須）
        hidden_fields: dict[str, str] = {}
        for _m in re.finditer(
            r'<input[^>]+type=["\']hidden["\'][^>]*name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\']',
            login_html, re.IGNORECASE
        ):
            hidden_fields[_m.group(1)] = _m.group(2)
        # value が先にくるパターンも対応
        for _m in re.finditer(
            r'<input[^>]+type=["\']hidden["\'][^>]*value=["\']([^"\']*)["\'][^>]*name=["\']([^"\']+)["\']',
            login_html, re.IGNORECASE
        ):
            hidden_fields.setdefault(_m.group(2), _m.group(1))
        _logger.debug(f"login hidden fields: {hidden_fields}")

        post_data: dict[str, str] = {
            **hidden_fields,
            "login_id": NETKEIBA_EMAIL,
            "pswd": NETKEIBA_PASSWORD,
        }
        if csrf_token:
            post_data["_token"] = csrf_token

        # ステップ2: POSTでログイン（まずリダイレクトなしで確認）
        await asyncio.sleep(1.0)  # INV-07: 1秒以上間隔
        async with session.post(
            _NETKEIBA_LOGIN_URL,
            data=post_data,
            allow_redirects=False,
            headers={
                "Referer": _NETKEIBA_LOGIN_URL,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        ) as post_resp_nr:
            _post_status = post_resp_nr.status
            _post_location = post_resp_nr.headers.get("Location", "")
            _post_set_cookie = post_resp_nr.headers.getall("Set-Cookie", [])
            _logger.debug(
                f"login POST status={_post_status} "
                f"location={_post_location!r} "
                f"set-cookie count={len(_post_set_cookie)}"
            )

        # クッキーが設定されていれば（302リダイレクト）ログイン成功
        _cookies_after = list(session.cookie_jar)
        _has_auth_cookie = any(
            c.key in ("nkauth", "netkeiba", "member_sid") for c in _cookies_after
        )

        if _post_status in (302, 301) or _has_auth_cookie:
            # ログイン成功 → リダイレクト先をGETしてセッションを確立
            if _post_location:
                await asyncio.sleep(0.5)
                try:
                    async with session.get(
                        _post_location,
                        allow_redirects=True,
                        headers={"Referer": _NETKEIBA_LOGIN_URL},
                    ) as _follow_resp:
                        await _follow_resp.read()  # ページ読み捨て
                except Exception as _fe:
                    _logger.debug(f"redirect follow failed (non-fatal): {_fe}")
            _logger.info("netkeiba ログイン成功（プレミアム会員）")
            _netkeiba_logged_in = True
            return True

        # status=200 のままの場合はログイン失敗（フォームが返ってきた）
        _logger.error(
            f"netkeiba ログイン失敗: POST status={_post_status}、"
            "クッキーが設定されませんでした。認証情報を確認してください。"
        )
        return False

    except Exception as e:
        _logger.warning(f"netkeiba ログインエラー: {e}")
        return False

