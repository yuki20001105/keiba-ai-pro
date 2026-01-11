"""
HTMLを直接取得して内容を確認
"""
from pathlib import Path
from keiba_ai.config import load_config
from keiba_ai.netkeiba.client import NetkeibaClient
from keiba_ai.netkeiba.parsers import extract_race_ids

def debug_html_fetch():
    print("=" * 80)
    print("HTML取得デバッグ")
    print("=" * 80)
    
    cfg = load_config("config.yaml")
    client = NetkeibaClient(cfg.netkeiba, cfg.storage)
    
    # 2024年12月29日（有馬記念）
    date_str = "20241229"
    
    print(f"\n📡 {date_str}のHTMLを取得...")
    
    url = f"{cfg.netkeiba.base}/top/race_list.html?kaisai_date={date_str}"
    print(f"URL: {url}")
    
    result = client.fetch_html(url, cache_kind="list", cache_key=f"debug_{date_str}", use_cache=False)
    
    print(f"\nステータス: {result.status_code}")
    print(f"キャッシュから: {result.from_cache}")
    print(f"HTML長: {len(result.text)} 文字")
    
    # HTMLの最初の1000文字を表示
    print(f"\n--- HTML冒頭 (1000文字) ---")
    print(result.text[:1000])
    print("...")
    
    # race_idパターンを探す
    print(f"\n--- race_idパターン検索 ---")
    race_ids = extract_race_ids(result.text)
    
    if race_ids:
        print(f"✅ {len(race_ids)}件のrace_idを検出:")
        for rid in race_ids[:10]:
            print(f"   - {rid}")
        if len(race_ids) > 10:
            print(f"   ... 他{len(race_ids) - 10}件")
    else:
        print(f"❌ race_idが見つかりませんでした")
        
        # パターンを手動で探す
        import re
        print(f"\n--- 12桁の数字を検索 ---")
        twelve_digits = re.findall(r'\b(\d{12})\b', result.text)
        if twelve_digits:
            print(f"見つかった12桁の数字:")
            for d in set(twelve_digits[:20]):
                print(f"   - {d}")
        else:
            print("12桁の数字が見つかりませんでした")
    
    # HTMLをファイルに保存
    output_file = Path("data/html/list") / f"debug_{date_str}.html"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(result.text, encoding='utf-8')
    print(f"\n💾 HTMLを保存: {output_file}")

if __name__ == "__main__":
    debug_html_fetch()
