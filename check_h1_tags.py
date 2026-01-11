import requests
from bs4 import BeautifulSoup

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

race_id = '202412220612'
url = f'https://race.netkeiba.com/race/result.html?race_id={race_id}'

response = requests.get(url, headers=headers)
soup = BeautifulSoup(response.text, 'html.parser')

print(f'Status: {response.status_code}\n')

# すべてのh1タグ
print('All H1 tags:')
h1_tags = soup.find_all('h1')
for h1 in h1_tags:
    text = h1.text.strip()
    if text:
        print(f'  Class: {h1.get("class")}, Text: {text[:80]}')

# ページのタイトルを確認
title = soup.find('title')
if title:
    print(f'\nPage Title: {title.text}')

# HTMLの一部を保存
with open('result_html_sample.html', 'w', encoding='utf-8') as f:
    # 最初の5000文字を保存
    f.write(response.text[:5000])
    
print('\nHTML sample saved to result_html_sample.html')
