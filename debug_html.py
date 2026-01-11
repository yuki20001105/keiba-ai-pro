"""
netkeiba.comのHTML構造を確認
"""
import requests

USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
BASE_URL = 'https://race.netkeiba.com'

# テスト用の日付（2024年12月1日）
test_date = '20241201'
url = f'{BASE_URL}/top/race_list.html?kaisai_date={test_date}'

print(f"URL: {url}")
print(f"\nHTML取得中...")

response = requests.get(url, headers={'User-Agent': USER_AGENT}, timeout=30)
print(f"ステータス: {response.status_code}")

if response.status_code == 200:
    html = response.text
    
    # 保存して確認
    with open('netkeiba_html_debug.html', 'w', encoding='utf-8') as f:
        f.write(html)
    
    print(f"✅ HTMLを netkeiba_html_debug.html に保存しました")
    print(f"HTML長さ: {len(html)} 文字")
    
    # キーワード検索
    print(f"\n【キーワード検索】")
    keywords = ['RaceList', 'race_id', 'RaceData', 'RaceTitle', 'RaceNum']
    for keyword in keywords:
        count = html.count(keyword)
        print(f"  '{keyword}': {count}回出現")
    
    # race_id を含む行を抽出
    print(f"\n【race_id を含む行（最初の5行）】")
    lines_with_race_id = [line.strip() for line in html.split('\n') if 'race_id=' in line]
    for i, line in enumerate(lines_with_race_id[:5], 1):
        print(f"{i}. {line[:150]}...")
        
else:
    print(f"❌ エラー: HTTP {response.status_code}")
