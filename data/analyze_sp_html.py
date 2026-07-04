"""SP HTMLからAPIエンドポイントを探す"""
import re

with open('data/sp_odds_page.html', encoding='utf-8') as f:
    html = f.read()

# 全 script src
scripts = re.findall(r'src="(https?[^"]+)"', html)
print('Script sources:')
for s in scripts:
    if 'netkeiba' in s or 'odds' in s.lower():
        print(f'  {s}')

# race.sp.netkeiba の全URL
sp_urls = re.findall(r'race\.sp\.netkeiba\.com[^"\'<>\s]*', html)
print('\nSP URLs found:')
for u in set(sp_urls):
    print(f'  {u}')

# api.netkeiba / netkeiba API URLs
api_urls = re.findall(r'"(https?://[^"]*(?:api|odds)[^"]*)"', html, re.IGNORECASE)
print('\nAPI URLs:')
for u in set(api_urls):
    if 'netkeiba' in u or 'jra' in u.lower():
        print(f'  {u}')

# inline JS 全文チェック
inline = re.findall(r'<script(?![^>]*src)[^>]*>(.*?)</script>', html, re.DOTALL | re.IGNORECASE)
print(f'\nInline scripts: {len(inline)}')
for i, s in enumerate(inline):
    s = s.strip()
    if s and ('odds' in s.lower() or 'ajax' in s.lower()):
        print(f'--- inline[{i}] len={len(s)} ---')
        print(s[:800])
