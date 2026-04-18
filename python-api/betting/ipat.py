"""
IPAT（JRA-NET）自動投票モジュール

概要:
  JRA の電話投票サービス（IPAT）の Web 画面を Playwright で操作し、
  bet_export.py が生成した買い目リストを自動投票する。

  ⚠️  デフォルトは dry_run=True（実際の購入は行わない）。
      本番購入には IPATVoter(..., dry_run=False) かつ
      execute_vote.py --no-dry-run が必要。

認証情報の管理:
  .env ファイル（絶対にリポジトリへコミットしない）から読み込む。
  必要なキー:
    IPAT_INET_ID     : 加入者番号（数字8桁）
    IPAT_USER_ID     : INET-ID（ユーザーID、英数字）
    IPAT_PASSWORD    : パスワード
    IPAT_PARS        : P-ARS 番号（4桁）

依存:
    playwright>=1.40    (python-api/.venv に既インストール済み)
    python-dotenv>=1.0  (環境変数ロード)
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 環境変数ロード（.env があれば読み込む）
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv
    _ENV_FILE = Path(__file__).parent.parent.parent / ".env"
    if _ENV_FILE.exists():
        load_dotenv(str(_ENV_FILE))
        logger.debug(f"[ipat] .env を読み込みました: {_ENV_FILE}")
except ImportError:
    logger.warning("[ipat] python-dotenv 未インストール。環境変数を直接設定してください。")


# ---------------------------------------------------------------------------
# データクラス
# ---------------------------------------------------------------------------

@dataclass
class BetOrder:
    """1件の購入指示"""
    race_id: str
    venue: str
    race_no: int
    post_time: str          # "HH:MM" 形式
    bet_type_code: str      # "tan" / "umaren" / etc.
    combination: str        # "5" / "3-5" / "2-5-8" etc.
    units: int              # 購入単位数
    unit_price: int = 100   # 1単位の金額（通常100円）
    expected_value: float = 0.0
    horse_names: list[str] = field(default_factory=list)

    @property
    def total_cost(self) -> int:
        return self.units * self.unit_price


@dataclass
class VoteResult:
    """投票結果"""
    order: BetOrder
    success: bool
    message: str = ""
    receipt_no: str = ""    # 購入番号（成功時）
    dry_run: bool = True


# ---------------------------------------------------------------------------
# IPATVoter クラス
# ---------------------------------------------------------------------------

class IPATVoter:
    """
    JRA IPAT （電話投票ネット版）自動投票クライアント。

    使用例:
        voter = IPATVoter(dry_run=True)          # デフォルト: 実購入しない
        results = await voter.vote(bet_orders)
        for r in results:
            print(r.success, r.message)
    """

    # IPAT トップページ URL
    _IPAT_URL = "https://www.jra.go.jp/IPAT/"
    # 馬券種コード → IPATセレクト値（暫定。HTML確認後に調整が必要）
    _BET_TYPE_MAP = {
        "tan":       "1",   # 単勝
        "fuku":      "2",   # 複勝
        "wakuren":   "3",   # 枠連
        "umaren":    "4",   # 馬連
        "wide":      "5",   # ワイド
        "umatan":    "6",   # 馬単
        "sanrenpuku": "7",  # 三連複
        "sanrentan": "8",   # 三連単
    }

    def __init__(
        self,
        dry_run: bool = True,
        headless: bool = True,
        timeout_ms: int = 30_000,
    ) -> None:
        """
        Args:
            dry_run:    True の場合、投票確認画面まで進んで実際の購入は行わない（安全デフォルト）。
            headless:   True でブラウザをヘッドレス起動（サーバー向け）。
            timeout_ms: 各操作のタイムアウト（ms）。
        """
        self.dry_run = dry_run
        self.headless = headless
        self.timeout_ms = timeout_ms

        # 認証情報（.env から取得）
        self._inet_id   = os.environ.get("IPAT_INET_ID", "")
        self._user_id   = os.environ.get("IPAT_USER_ID", "")
        self._password  = os.environ.get("IPAT_PASSWORD", "")
        self._pars      = os.environ.get("IPAT_PARS", "")

        if not all([self._inet_id, self._user_id, self._password, self._pars]):
            logger.warning(
                "[ipat] 認証情報が未設定です。"
                " .env に IPAT_INET_ID / IPAT_USER_ID / IPAT_PASSWORD / IPAT_PARS を設定してください。"
            )

    # ── ログイン ─────────────────────────────────────────────────────────

    async def _login(self, page: Any) -> None:
        """IPAT ログイン画面を操作してセッションを確立する。"""
        logger.info("[ipat] ログインページへ移動")
        await page.goto(self._IPAT_URL, timeout=self.timeout_ms)
        await page.wait_for_load_state("domcontentloaded")

        # 加入者番号 / INET-ID / パスワード を入力
        # ⚠️ セレクタは IPAT 実際の HTML に合わせて調整が必要
        try:
            await page.fill('input[name="inetid"]', self._inet_id, timeout=self.timeout_ms)
            await page.fill('input[name="userid"]', self._user_id, timeout=self.timeout_ms)
            await page.fill('input[name="pass"]',   self._password, timeout=self.timeout_ms)
            await page.click('input[type="submit"]', timeout=self.timeout_ms)
            await page.wait_for_load_state("domcontentloaded")
            logger.info("[ipat] ログイン送信完了")
        except Exception as e:
            raise RuntimeError(f"[ipat] ログイン操作に失敗: {e}") from e

    # ── 馬券入力 ─────────────────────────────────────────────────────────

    async def _enter_bet(self, page: Any, order: BetOrder) -> None:
        """馬券種・馬番・金額を入力し「セット」ボタンを押す。"""
        bet_code = self._BET_TYPE_MAP.get(order.bet_type_code, "1")
        logger.info(
            f"[ipat] 馬券入力 race_id={order.race_id} "
            f"type={order.bet_type_code} comb={order.combination} units={order.units}"
        )

        try:
            # 馬券種選択
            await page.select_option('select[name="bettype"]', bet_code, timeout=self.timeout_ms)

            # 馬番入力（組み合わせ "-" 区切りを分解）
            nums = [n.strip() for n in order.combination.split("-")]
            for i, num in enumerate(nums[:3]):
                await page.fill(f'input[name="horse{i+1}"]', num, timeout=self.timeout_ms)

            # 購入単位（100円単位）
            await page.fill('input[name="purchase"]', str(order.units), timeout=self.timeout_ms)

            # セットボタン
            await page.click('button#set-btn, input[value="セット"]', timeout=self.timeout_ms)
            await page.wait_for_load_state("domcontentloaded")
        except Exception as e:
            raise RuntimeError(f"[ipat] 馬券入力に失敗 ({order.combination}): {e}") from e

    # ── 購入実行 ─────────────────────────────────────────────────────────

    async def _confirm_purchase(self, page: Any) -> str:
        """
        確認画面で購入ボタンを押す（dry_run=False のみ）。
        戻り値: 購入番号（取得できた場合）
        """
        if self.dry_run:
            logger.info("[ipat] dry_run=True → 購入確認画面で停止（実際の購入なし）")
            return ""

        try:
            await page.click('button#purchase-btn, input[value="購入"]', timeout=self.timeout_ms)
            await page.wait_for_load_state("domcontentloaded")

            # 購入番号を取得（HTML 要素名は要確認）
            receipt_el = page.locator('#receipt-no, .receipt-number').first
            receipt_no = await receipt_el.inner_text() if await receipt_el.count() > 0 else ""
            logger.info(f"[ipat] 購入完了 receipt={receipt_no}")
            return receipt_no.strip()
        except Exception as e:
            raise RuntimeError(f"[ipat] 購入確認操作に失敗: {e}") from e

    # ── メイン投票エントリ ────────────────────────────────────────────────

    async def vote(self, orders: list[BetOrder]) -> list[VoteResult]:
        """
        指定された BetOrder リストを順番に投票する。

        Args:
            orders: `execute_vote.py` が生成する BetOrder のリスト。

        Returns:
            各 order に対する VoteResult のリスト。
        """
        if not orders:
            logger.info("[ipat] 投票注文が空です")
            return []

        if not all([self._inet_id, self._user_id, self._password, self._pars]):
            logger.error("[ipat] 認証情報が未設定のため投票をスキップします")
            return [
                VoteResult(order=o, success=False, message="認証情報未設定", dry_run=self.dry_run)
                for o in orders
            ]

        results: list[VoteResult] = []

        try:
            from playwright.async_api import async_playwright  # type: ignore
        except ImportError:
            logger.error("[ipat] playwright が未インストールです。pip install playwright を実行してください。")
            return [
                VoteResult(order=o, success=False, message="playwright未インストール", dry_run=self.dry_run)
                for o in orders
            ]

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self.headless)
            context = await browser.new_context()
            page    = await context.new_page()

            try:
                await self._login(page)

                for order in orders:
                    try:
                        await self._enter_bet(page, order)
                        receipt_no = await self._confirm_purchase(page)
                        results.append(VoteResult(
                            order=order,
                            success=True,
                            message="購入完了" if not self.dry_run else "ドライラン完了",
                            receipt_no=receipt_no,
                            dry_run=self.dry_run,
                        ))
                        logger.info(
                            f"[ipat] ✓ {order.race_id} {order.bet_type_code} "
                            f"{order.combination} ×{order.units} = ¥{order.total_cost:,}"
                            + (" [DRY RUN]" if self.dry_run else f" receipt={receipt_no}")
                        )
                    except Exception as e:
                        results.append(VoteResult(
                            order=order,
                            success=False,
                            message=str(e),
                            dry_run=self.dry_run,
                        ))
                        logger.error(f"[ipat] ✗ {order.race_id} {order.bet_type_code} {order.combination}: {e}")

            finally:
                await browser.close()

        return results


# ---------------------------------------------------------------------------
# ユーティリティ: bet_export の dict を BetOrder に変換
# ---------------------------------------------------------------------------

def bet_row_to_order(row: dict[str, Any]) -> BetOrder:
    """
    `bet_export.py` の `_build_bet_rows()` が返す dict を BetOrder に変換する。
    """
    return BetOrder(
        race_id      = str(row.get("race_id", "")),
        venue        = str(row.get("venue", "")),
        race_no      = int(row.get("race_no") or 0),
        post_time    = str(row.get("post_time", "")),
        bet_type_code= str(row.get("bet_type_code", "tan")),
        combination  = str(row.get("combination", "")),
        units        = int(row.get("units") or 1),
        unit_price   = int(row.get("unit_price") or 100),
        expected_value = float(row.get("expected_value") or 0.0),
        horse_names  = list(row.get("horse_names") or []),
    )
