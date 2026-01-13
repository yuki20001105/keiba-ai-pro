#!/usr/bin/env python3
"""
Streamlit版を参考にしたシンプルなスクレイピングスクリプト
使い方:
  python simple_scrape.py --date 20240101
  python simple_scrape.py --race 202406010101
"""
import argparse
import requests
import sys
from datetime import datetime


SCRAPING_API_BASE = "http://localhost:8001"


def ingest_by_date(date_str: str) -> list[str]:
    """
    日付を指定してレースID一覧を取得（Streamlit版のingest_by_dateと同じ）
    
    Args:
        date_str: YYYYMMDD形式の日付
    
    Returns:
        レースIDのリスト
    """
    print(f"\n📅 {date_str} のレース一覧を取得中...")
    
    try:
        response = requests.post(
            f"{SCRAPING_API_BASE}/scrape/race_list",
            json={"kaisai_date": date_str},
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        
        if data.get('success') and data.get('race_ids'):
            race_ids = data['race_ids']
            print(f"✅ {len(race_ids)} レースを発見:")
            for rid in race_ids:
                print(f"  - {rid}")
            return race_ids
        else:
            print("❌ レースが見つかりませんでした")
            return []
    
    except requests.exceptions.ConnectionError:
        print(f"❌ エラー: スクレイピングサービスに接続できません")
        print(f"💡 解決策: 'npm run dev:all' を実行してサービスを起動してください")
        sys.exit(1)
    except Exception as e:
        print(f"❌ エラー: {e}")
        return []


def ingest_one_race(race_id: str, include_details: bool = True) -> dict:
    """
    個別レースのデータを取得（Streamlit版のingest_one_raceと同じ）
    
    Args:
        race_id: 12桁のレースID
        include_details: 詳細ページも取得するか（False=高速モード）
    
    Returns:
        スクレイピング結果の辞書
    """
    print(f"\n🏇 レース {race_id} のデータ取得中...")
    print(f"   詳細モード: {'ON' if include_details else 'OFF（高速）'}")
    
    try:
        response = requests.post(
            f"{SCRAPING_API_BASE}/scrape/ultimate",
            json={
                "race_id": race_id,
                "include_details": include_details
            },
            timeout=120  # 詳細モードは時間がかかる
        )
        response.raise_for_status()
        data = response.json()
        
        if data.get('success'):
            print(f"✅ 成功: {data.get('race_name', 'Unknown')}")
            print(f"   馬数: {len(data.get('results', []))} 頭")
            print(f"   払戻: {len(data.get('payouts', []))} 種類")
            return data
        else:
            error_msg = data.get('error', 'Unknown error')
            print(f"❌ 失敗: {error_msg}")
            return {"success": False, "error": error_msg}
    
    except requests.exceptions.ConnectionError:
        print(f"❌ エラー: スクレイピングサービスに接続できません")
        print(f"💡 解決策: 'npm run dev:all' を実行してサービスを起動してください")
        sys.exit(1)
    except requests.exceptions.Timeout:
        print(f"❌ エラー: タイムアウト（120秒超過）")
        return {"success": False, "error": "Timeout"}
    except Exception as e:
        print(f"❌ エラー: {e}")
        return {"success": False, "error": str(e)}


def save_to_supabase(race_data: dict, user_id: str) -> bool:
    """
    Next.js APIを通じてSupabaseに保存
    
    Args:
        race_data: スクレイピング結果
        user_id: ユーザーID
    
    Returns:
        保存成功ならTrue
    """
    print(f"\n💾 Supabaseに保存中...")
    
    try:
        response = requests.post(
            "http://localhost:3000/api/netkeiba/race",
            json={
                "raceId": race_data.get('race_id'),
                "userId": user_id
            },
            timeout=30
        )
        response.raise_for_status()
        result = response.json()
        
        if result.get('success'):
            print(f"✅ 保存成功")
            print(f"   結果: {result.get('resultsCount', 0)} 件")
            print(f"   払戻: {result.get('payoutsCount', 0)} 件")
            return True
        else:
            print(f"❌ 保存失敗: {result.get('error', 'Unknown')}")
            return False
    
    except requests.exceptions.ConnectionError:
        print(f"❌ エラー: Next.jsサーバーに接続できません")
        print(f"💡 解決策: 'npm run dev:all' を実行してサーバーを起動してください")
        return False
    except Exception as e:
        print(f"❌ エラー: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Streamlit版を参考にしたシンプルなスクレイピングツール"
    )
    parser.add_argument(
        "--date",
        help="日付指定（YYYYMMDD形式）。例: 20240101"
    )
    parser.add_argument(
        "--race",
        help="レースID指定（12桁）。例: 202406010101"
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="高速モード（詳細ページをスキップ）"
    )
    parser.add_argument(
        "--user-id",
        default="default-user-id",
        help="ユーザーID（Supabase保存用）"
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Supabaseへの保存をスキップ"
    )
    
    args = parser.parse_args()
    
    if not args.date and not args.race:
        parser.print_help()
        print("\n❌ エラー: --date または --race のいずれかを指定してください")
        sys.exit(1)
    
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  🏇 競馬データスクレイピング")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    
    # 日付指定の場合
    if args.date:
        race_ids = ingest_by_date(args.date)
        if not race_ids:
            print("\n⚠️ レースが見つかりませんでした")
            sys.exit(0)
        
        print(f"\n{'='*60}")
        print(f"📊 {len(race_ids)} レースのデータを取得します")
        print(f"{'='*60}")
        
        success_count = 0
        for i, race_id in enumerate(race_ids, 1):
            print(f"\n[{i}/{len(race_ids)}] {race_id}")
            print("-" * 60)
            
            race_data = ingest_one_race(race_id, include_details=not args.fast)
            
            if race_data.get('success'):
                if not args.no_save:
                    if save_to_supabase(race_data, args.user_id):
                        success_count += 1
                else:
                    success_count += 1
        
        print(f"\n{'='*60}")
        print(f"✅ 完了: {success_count}/{len(race_ids)} レース成功")
        print(f"{'='*60}")
    
    # レースID指定の場合
    elif args.race:
        race_data = ingest_one_race(args.race, include_details=not args.fast)
        
        if race_data.get('success'):
            if not args.no_save:
                save_to_supabase(race_data, args.user_id)
            print("\n✅ 完了")
        else:
            print("\n❌ 失敗")
            sys.exit(1)


if __name__ == "__main__":
    main()
