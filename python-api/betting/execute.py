"""
自動投票 CLI スクリプト（execute_vote.py）

概要:
  FastAPI サーバー（localhost:8000）の予測 API を呼び出し、
  APScheduler で発走 270 秒前に IPAT 自動投票を実行する。

前提:
  本スクリプトは FastAPI サーバーが起動している状態で使用する。
  サーバー起動: python main.py  または  uvicorn main:app --port 8000

使用例:
  # ドライラン（実際の購入なし）で今日のレースをスケジュール
  python execute_vote.py

  # 本番購入（要: .env に IPAT_* 設定済み）
  python execute_vote.py --date 20260412 --no-dry-run --headless

  # 本番投票、最低期待値 1.3 以上のみ
  python execute_vote.py --date 20260412 --no-dry-run --min-ev 1.3

引数:
  --date        開催日（YYYYMMDD）。省略時は今日。
  --api-url     FastAPI サーバー URL（デフォルト: http://localhost:8000）
  --dev         開発モード別名。dry_run=True と同義（デフォルト ON）。
  --no-dry-run  実際の購入を行う（--dev と同時使用不可）。
  --headless    ブラウザをヘッドレス起動（デフォルト ON）。
  --no-headless ヘッドレス無効（GUI 確認用）。
  --min-ev      最低期待値フィルタ（デフォルト 1.0）。
  --bankroll    総資金（デフォルト 10000円）。
  --lead-sec    発走何秒前に投票を実行するか（デフォルト 270）。
  --model-id    使用するモデル ID（省略時は最新モデル）。

⚠️  安全設計:
  - デフォルトは dry_run=True（--no-dry-run を明示しない限り購入しない）
  - 認証情報は .env ファイル（リポジトリ外）からのみ取得
  - ログは logs/execute_vote_YYYYMMDD.log に保存

cron 設定例（毎土日 8:00 JST に起動）:
  0 8 * * 6,0  cd /keiba-ai-pro/python-api &&
    .venv/bin/python execute_vote.py --date $(date +%%Y%%m%%d) --no-dry-run >> logs/cron.log 2>&1
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

# ── ログ設定 ─────────────────────────────────────────────────────────────
_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)

_log_file = _LOG_DIR / f"execute_vote_{datetime.now().strftime('%Y%m%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s:%(message)s",
    handlers=[
        logging.FileHandler(str(_log_file), mode="a", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
# 外部ライブラリの冗長ログを抑制
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("playwright").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 引数パーサ
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="競馬AI 自動投票スケジューラー",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--date",       default=None,  help="開催日 YYYYMMDD（省略時: 今日）")
    p.add_argument("--api-url",    default="http://localhost:8000", dest="api_url",
                   help="FastAPI サーバー URL")
    p.add_argument("--dev",        action="store_true", help="開発モード（dry_run=True と同義）")
    p.add_argument("--no-dry-run", action="store_true", dest="no_dry_run",
                   help="実際の IPAT 購入を実行する（要: .env に認証情報）")
    p.add_argument("--headless",   action=argparse.BooleanOptionalAction, default=True,
                   help="ブラウザをヘッドレスで起動")
    p.add_argument("--min-ev",     type=float, default=1.0, dest="min_ev",
                   help="最低期待値フィルタ")
    p.add_argument("--bankroll",   type=int,   default=10_000,
                   help="総資金（円）")
    p.add_argument("--lead-sec",   type=int,   default=270, dest="lead_sec",
                   help="発走何秒前に投票ジョブを実行するか")
    p.add_argument("--model-id",   default=None, dest="model_id",
                   help="使用する学習済みモデル ID（省略時は最新）")
    return p


# ---------------------------------------------------------------------------
# API ヘルパー: FastAPI サーバーへの HTTP 呼び出し
# ---------------------------------------------------------------------------

async def _fetch_race_timetable(api_url: str, date_str: str) -> list[dict]:
    """
    GET /api/races/by_date?date=YYYYMMDD を呼び出してレース一覧を取得する。
    post_time が不明なレースは除外する。
    """
    import aiohttp

    url = f"{api_url}/api/races/by_date"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params={"date": date_str}, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    logger.error(f"[timetable] API エラー {resp.status}: {url}")
                    return []
                races: list[dict] = await resp.json()
    except aiohttp.ClientConnectorError:
        logger.error(
            f"[timetable] FastAPI サーバーに接続できません ({api_url})。"
            " まず 'python main.py' でサーバーを起動してください。"
        )
        return []
    except Exception as e:
        logger.error(f"[timetable] 取得エラー: {e}")
        return []

    result = []
    today = datetime.strptime(date_str, "%Y%m%d")
    for race in races:
        pt_raw = race.get("post_time") or race.get("race_time") or ""
        post_time: datetime | None = None
        for fmt in ("%H:%M", "%H時%M分", "%H:%M:%S"):
            try:
                t = datetime.strptime(str(pt_raw).strip(), fmt)
                post_time = today.replace(hour=t.hour, minute=t.minute, second=0)
                break
            except ValueError:
                continue
        if post_time is None:
            logger.debug(f"[timetable] post_time 不明: {race.get('race_id')} → スキップ")
            continue
        result.append({**race, "post_time": post_time})

    logger.info(f"[timetable] {date_str}: {len(result)} レース（post_time 判明分）")
    return result


async def _analyze_race(
    api_url: str,
    race_id: str,
    bankroll: int,
    model_id: str | None,
) -> dict | None:
    """
    POST /api/analyze_race を呼び出してレース予測を取得する。
    """
    import aiohttp

    url = f"{api_url}/api/analyze_race"
    payload: dict = {"race_id": race_id, "bankroll": bankroll}
    if model_id:
        payload["model_id"] = model_id

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.warning(f"[analyze] {race_id}: HTTP {resp.status} - {text[:200]}")
                    return None
                return await resp.json()
    except aiohttp.ClientConnectorError:
        logger.error(f"[analyze] FastAPI サーバーに接続できません: {api_url}")
        return None
    except Exception as e:
        logger.error(f"[analyze] {race_id}: 予測エラー: {e}")
        return None


# ---------------------------------------------------------------------------
# 1 レース分の投票ジョブ
# ---------------------------------------------------------------------------

async def _vote_job(
    race_id: str,
    api_url: str,
    bankroll: int,
    min_ev: float,
    model_id: str | None,
    dry_run: bool,
    headless: bool,
) -> None:
    """
    APScheduler から呼び出されるジョブ。
    1. 予測 API 呼び出し
    2. 買い目生成
    3. IPAT 投票
    """
    logger.info(f"[job] 開始 race_id={race_id} dry_run={dry_run}")

    # ① 予測
    analyze_result = await _analyze_race(api_url, race_id, bankroll, model_id)
    if not analyze_result or not analyze_result.get("success"):
        logger.warning(f"[job] 予測失敗または結果なし: {race_id}")
        return

    # ② 買い目生成（bet_export の内部関数を直接呼び出す）
    try:
        _here = Path(__file__).resolve().parent.parent
        if str(_here) not in sys.path:
            sys.path.insert(0, str(_here))
        from routers.bet_export import _build_bet_rows  # type: ignore
    except ImportError as e:
        logger.error(f"[job] bet_export インポート失敗: {e}")
        return

    bet_rows = _build_bet_rows(
        [analyze_result],
        bankroll=bankroll,
        min_ev=min_ev,
        min_prob=0.0,
        max_bets_per_race=3,
    )
    if not bet_rows:
        logger.info(f"[job] 買い目なし（EV < {min_ev}）: {race_id}")
        return

    # ③ BetOrder に変換
    try:
        from betting.ipat import IPATVoter, bet_row_to_order  # type: ignore
    except ImportError as e:
        logger.error(f"[job] ipat_voter インポート失敗: {e}")
        return

    orders = [bet_row_to_order(r) for r in bet_rows]
    total_cost = sum(o.total_cost for o in orders)
    logger.info(
        f"[job] 投票予定: {len(orders)} 件 合計 ¥{total_cost:,}"
        + (" [DRY RUN]" if dry_run else " [本番]")
    )

    # ④ 投票
    voter = IPATVoter(dry_run=dry_run, headless=headless)
    results = await voter.vote(orders)

    # ⑤ 結果ログ
    success_count = sum(1 for r in results if r.success)
    for r in results:
        status = "✓" if r.success else "✗"
        logger.info(
            f"[job] {status} {r.order.race_id} {r.order.bet_type_code} "
            f"{r.order.combination} ×{r.order.units}: {r.message}"
        )
    logger.info(f"[job] 完了 race_id={race_id}: {success_count}/{len(results)} 件成功")


# ---------------------------------------------------------------------------
# メイン: APScheduler で発走前ジョブを登録
# ---------------------------------------------------------------------------

def main() -> None:
    parser = _build_parser()
    args   = parser.parse_args()

    # 日付決定
    date_str = args.date or datetime.now().strftime("%Y%m%d")

    # dry_run 判定（--dev OR デフォルト → dry_run=True、--no-dry-run → False）
    if args.dev and args.no_dry_run:
        parser.error("--dev と --no-dry-run は同時使用できません")
    dry_run = not args.no_dry_run  # デフォルト True で安全

    logger.info("=" * 60)
    logger.info(f"[execute_vote] 開始 date={date_str} dry_run={dry_run}")
    logger.info(f"  api_url={args.api_url} headless={args.headless}")
    logger.info(f"  min_ev={args.min_ev} bankroll={args.bankroll:,} lead_sec={args.lead_sec}")
    logger.info(f"  model_id={args.model_id}")
    logger.info(f"  ログファイル: {_log_file}")
    logger.info("=" * 60)

    if not dry_run:
        logger.warning("⚠️  本番モード: 実際のIPAT購入が実行されます")

    # APScheduler の確認
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler  # type: ignore
        from apscheduler.triggers.date import DateTrigger  # type: ignore
    except ImportError:
        logger.error("APScheduler が未インストールです。pip install APScheduler を実行してください。")
        sys.exit(1)

    # レース時刻表を取得（同期的に asyncio.run で呼ぶ）
    timetable = asyncio.run(_fetch_race_timetable(args.api_url, date_str))
    if not timetable:
        logger.error(
            f"[execute_vote] {date_str} のレースデータが取得できませんでした。"
            " FastAPI サーバーが起動しているか、またはスクレイプ済みか確認してください。"
        )
        sys.exit(1)

    scheduler = BlockingScheduler(timezone="Asia/Tokyo")
    now = datetime.now()
    registered = 0

    for race_info in timetable:
        post_time: datetime = race_info["post_time"]
        run_time  = post_time - timedelta(seconds=args.lead_sec)

        # 過去の発走時刻はスキップ
        if run_time <= now:
            logger.debug(f"[schedule] スキップ（過去）: {race_info['race_id']} run_time={run_time:%H:%M}")
            continue

        # デフォルト引数でクロージャのキャプチャを確定させる
        scheduler.add_job(
            lambda rid=race_info["race_id"]: asyncio.run(_vote_job(
                race_id   = rid,
                api_url   = args.api_url,
                bankroll  = args.bankroll,
                min_ev    = args.min_ev,
                model_id  = args.model_id,
                dry_run   = dry_run,
                headless  = args.headless,
            )),
            trigger=DateTrigger(run_date=run_time, timezone="Asia/Tokyo"),
            id=race_info["race_id"],
            replace_existing=True,
            misfire_grace_time=120,
        )
        logger.info(
            f"[schedule] {race_info['race_id']} @ {run_time:%H:%M:%S}"
            f" (発走 {post_time:%H:%M} の {args.lead_sec} 秒前)"
        )
        registered += 1

    if registered == 0:
        logger.warning(
            f"[execute_vote] スケジュール済みジョブが 0 件。"
            f" {date_str} のレースはすべて終了しているか、開催がありません。"
        )
        sys.exit(0)

    logger.info(f"[execute_vote] {registered} 件のジョブを登録しました。スケジューラー起動...")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("[execute_vote] スケジューラーを停止しました")


if __name__ == "__main__":
    main()
