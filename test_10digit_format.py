import requests

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

# 10桁フォーマット: YYYYMMDDRR (YYYY年MM月DD日 + 場コード1桁? + レース番号2桁)
# または YYYYMMDD + 場コード2桁なし？

# ユーザー提供の例: 202606010401
# これを分解すると: 2026 + 06 + 01 + 04 + 01
# = 2026年6月1日、場コード04（新潟）、1レース目

# 2023年1月7日（土）中山1Rを10桁で試す
# 中山 = 06
test_ids = [
    '2023010706' + '01',  # 2023/1/7 中山1R (10桁)
    '2023010705' + '01',  # 2023/1/7 東京1R (10桁) 
    '2023010806' + '01',  # 2023/1/8 中山1R (10桁)
]

print('Testing 10-digit race_id format: YYYYMMDD + venue + race')
print('='*80)

for race_id in test_ids:
    url = f'https://race.netkeiba.com/race/result.html?race_id={race_id}'
    
    print(f'\nrace_id: {race_id} ({len(race_id)} digits)')
    
    response = requests.get(url, headers=headers, timeout=10)
    print(f'Status: {response.status_code}')
    
    if response.status_code == 200:
        html = response.content.decode('euc-jp', errors='ignore')
        
        if '<title>' in html:
            start = html.find('<title>') + 7
            end = html.find('</title>')
            title = html[start:end].strip()
            
            cleaned = title.replace('|', '').replace('-', '').replace('netkeiba', '').strip()
            
            if cleaned:
                print(f'✓ FOUND! Title: {title[:80]}')
                print(f'\nCorrect format appears to be: 10 digits (YYYYMMDD + venue1 + race2)')
                break
            else:
                print('✗ Empty title')
