"""
Playwrightブラウザモードで実際にレースデータを取得するテスト
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from keiba_ai.ingest import ingest_one_race

def main():
    print("=" * 70)
    print("Playwrightブラウザモードでレースデータを取得")
    print("=" * 70)
    print()
    print("📌 テスト対象: 2024年6月5日 東京8R (race_id=202406050811)")
    print()
    print("⏳ データ取得開始...")
    print()
    
    try:
        # ブラウザモードで1レース取得
        ingest_one_race(
            cfg_path=Path("config.yaml"),
            race_id="202406050811",
            fetch_shutuba=True,
            fetch_result=True,
            use_browser=True  # ← Playwrightを使用
        )
        
        print()
        print("=" * 70)
        print("✅ 取得完了！")
        print()
        print("💾 データベースに保存されました:")
        print("   - 出馬表データ (entries)")
        print("   - 結果データ (results)")
        print()
        print("📊 次のステップ:")
        print("   1. Streamlit UI の「4_DB確認」で取得データを確認")
        print("   2. 「2_学習」で新しいモデルを学習")
        print("   3. 「3_予測」で精度を確認")
        
    except Exception as e:
        print()
        print("=" * 70)
        print(f"❌ エラー: {e}")
        print()
        import traceback
        traceback.print_exc()
        print()
        print("💡 確認事項:")
        print("   1. Playwrightがインストールされているか")
        print("      pip install playwright")
        print("      playwright install chromium")
        print("   2. インターネット接続が正常か")
        print("   3. Netkeibaサイトがアクセス可能か")


if __name__ == "__main__":
    main()
