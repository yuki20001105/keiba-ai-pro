#!/usr/bin/env python3
"""
スクレイピング検証スクリプト - 段階的にテスト
各方法を順番に試して、最も安定した方法を見つける

検証する方法:
1. requests + BeautifulSoup (最速・簡単)
2. undetected_chromedriver (ブロック回避)
3. Playwright (Streamlit版と同じ)
4. Selenium + Chrome (代替案)

使い方:
  python verify_scraping.py --method requests --race 202406010101
  python verify_scraping.py --method undetected --race 202406010101
  python verify_scraping.py --method playwright --race 202406010101
  python verify_scraping.py --test-all --race 202406010101
"""
import argparse
import sys
import time
from datetime import datetime


def test_requests_method(race_id: str) -> dict:
    """方法1: requests + BeautifulSoup（最速）"""
    print("\n" + "="*80)
    print("【方法1】requests + BeautifulSoup")
    print("="*80)
    
    try:
        import requests
        from bs4 import BeautifulSoup
        
        url = f'https://race.netkeiba.com/race/result.html?race_id={race_id}'
        print(f"URL: {url}")
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        start_time = time.time()
        response = requests.get(url, headers=headers, timeout=10)
        elapsed = time.time() - start_time
        
        print(f"ステータス: {response.status_code}")
        print(f"応答時間: {elapsed:.2f}秒")
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # レース名を取得
            race_name_tag = soup.find('div', class_='RaceName')
            race_name = race_name_tag.text.strip() if race_name_tag else "Unknown"
            
            # 結果テーブルを取得
            result_table = soup.find('table', class_='ResultRefund')
            if result_table:
                rows = result_table.find_all('tr')
                horse_count = len([r for r in rows if r.find('td', class_='Txt_l')])
                
                print(f"✅ 成功")
                print(f"   レース名: {race_name}")
                print(f"   馬数: {horse_count}")
                print(f"   HTMLサイズ: {len(response.text)} bytes")
                
                return {
                    'success': True,
                    'method': 'requests',
                    'race_name': race_name,
                    'horse_count': horse_count,
                    'elapsed': elapsed,
                    'html_size': len(response.text)
                }
            else:
                print(f"❌ 失敗: 結果テーブルが見つかりません")
                return {'success': False, 'error': 'No result table'}
        else:
            print(f"❌ 失敗: HTTP {response.status_code}")
            return {'success': False, 'error': f'HTTP {response.status_code}'}
    
    except Exception as e:
        print(f"❌ エラー: {e}")
        return {'success': False, 'error': str(e)}


def test_undetected_method(race_id: str) -> dict:
    """方法2: undetected_chromedriver（ブロック回避）"""
    print("\n" + "="*80)
    print("【方法2】undetected_chromedriver")
    print("="*80)
    
    try:
        import undetected_chromedriver as uc
        from bs4 import BeautifulSoup
        
        print("Chromeドライバー起動中...")
        options = uc.ChromeOptions()
        options.add_argument('--headless')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        
        driver = uc.Chrome(options=options)
        
        url = f'https://race.netkeiba.com/race/result.html?race_id={race_id}'
        print(f"URL: {url}")
        
        start_time = time.time()
        driver.get(url)
        time.sleep(3)  # ページ読み込み待機
        elapsed = time.time() - start_time
        
        page_source = driver.page_source
        soup = BeautifulSoup(page_source, 'html.parser')
        
        # レース名を取得
        race_name_tag = soup.find('div', class_='RaceName')
        race_name = race_name_tag.text.strip() if race_name_tag else "Unknown"
        
        # 結果テーブルを取得
        result_table = soup.find('table', class_='ResultRefund')
        if result_table:
            rows = result_table.find_all('tr')
            horse_count = len([r for r in rows if r.find('td', class_='Txt_l')])
            
            driver.quit()
            
            print(f"✅ 成功")
            print(f"   レース名: {race_name}")
            print(f"   馬数: {horse_count}")
            print(f"   処理時間: {elapsed:.2f}秒")
            
            return {
                'success': True,
                'method': 'undetected_chromedriver',
                'race_name': race_name,
                'horse_count': horse_count,
                'elapsed': elapsed
            }
        else:
            driver.quit()
            print(f"❌ 失敗: 結果テーブルが見つかりません")
            return {'success': False, 'error': 'No result table'}
    
    except ImportError:
        print(f"❌ エラー: undetected_chromedriverがインストールされていません")
        print(f"💡 インストール: pip install undetected-chromedriver")
        return {'success': False, 'error': 'Module not installed'}
    except Exception as e:
        print(f"❌ エラー: {e}")
        return {'success': False, 'error': str(e)}


def test_playwright_method(race_id: str) -> dict:
    """方法3: Playwright（Streamlit版と同じ）"""
    print("\n" + "="*80)
    print("【方法3】Playwright (Streamlit版)")
    print("="*80)
    
    try:
        from playwright.sync_api import sync_playwright
        from bs4 import BeautifulSoup
        
        print("Playwright起動中...")
        
        start_time = time.time()
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            url = f'https://race.netkeiba.com/race/result.html?race_id={race_id}'
            print(f"URL: {url}")
            
            page.goto(url, wait_until='networkidle', timeout=30000)
            page_source = page.content()
            
            browser.close()
        
        elapsed = time.time() - start_time
        
        soup = BeautifulSoup(page_source, 'html.parser')
        
        # レース名を取得
        race_name_tag = soup.find('div', class_='RaceName')
        race_name = race_name_tag.text.strip() if race_name_tag else "Unknown"
        
        # 結果テーブルを取得
        result_table = soup.find('table', class_='ResultRefund')
        if result_table:
            rows = result_table.find_all('tr')
            horse_count = len([r for r in rows if r.find('td', class_='Txt_l')])
            
            print(f"✅ 成功")
            print(f"   レース名: {race_name}")
            print(f"   馬数: {horse_count}")
            print(f"   処理時間: {elapsed:.2f}秒")
            
            return {
                'success': True,
                'method': 'playwright',
                'race_name': race_name,
                'horse_count': horse_count,
                'elapsed': elapsed
            }
        else:
            print(f"❌ 失敗: 結果テーブルが見つかりません")
            return {'success': False, 'error': 'No result table'}
    
    except ImportError:
        print(f"❌ エラー: Playwrightがインストールされていません")
        print(f"💡 インストール: pip install playwright && playwright install chromium")
        return {'success': False, 'error': 'Module not installed'}
    except Exception as e:
        print(f"❌ エラー: {e}")
        return {'success': False, 'error': str(e)}


def test_selenium_method(race_id: str) -> dict:
    """方法4: Selenium + Chrome（代替案）"""
    print("\n" + "="*80)
    print("【方法4】Selenium + Chrome")
    print("="*80)
    
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from bs4 import BeautifulSoup
        
        print("Selenium起動中...")
        
        options = Options()
        options.add_argument('--headless')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        
        driver = webdriver.Chrome(options=options)
        
        url = f'https://race.netkeiba.com/race/result.html?race_id={race_id}'
        print(f"URL: {url}")
        
        start_time = time.time()
        driver.get(url)
        time.sleep(3)  # ページ読み込み待機
        elapsed = time.time() - start_time
        
        page_source = driver.page_source
        driver.quit()
        
        soup = BeautifulSoup(page_source, 'html.parser')
        
        # レース名を取得
        race_name_tag = soup.find('div', class_='RaceName')
        race_name = race_name_tag.text.strip() if race_name_tag else "Unknown"
        
        # 結果テーブルを取得
        result_table = soup.find('table', class_='ResultRefund')
        if result_table:
            rows = result_table.find_all('tr')
            horse_count = len([r for r in rows if r.find('td', class_='Txt_l')])
            
            print(f"✅ 成功")
            print(f"   レース名: {race_name}")
            print(f"   馬数: {horse_count}")
            print(f"   処理時間: {elapsed:.2f}秒")
            
            return {
                'success': True,
                'method': 'selenium',
                'race_name': race_name,
                'horse_count': horse_count,
                'elapsed': elapsed
            }
        else:
            print(f"❌ 失敗: 結果テーブルが見つかりません")
            return {'success': False, 'error': 'No result table'}
    
    except ImportError:
        print(f"❌ エラー: Seleniumがインストールされていません")
        print(f"💡 インストール: pip install selenium")
        return {'success': False, 'error': 'Module not installed'}
    except Exception as e:
        print(f"❌ エラー: {e}")
        return {'success': False, 'error': str(e)}


def main():
    parser = argparse.ArgumentParser(
        description="スクレイピング検証 - 各方法をテスト"
    )
    parser.add_argument(
        "--method",
        choices=['requests', 'undetected', 'playwright', 'selenium'],
        help="テストする方法"
    )
    parser.add_argument(
        "--race",
        required=True,
        help="テストするレースID（12桁）"
    )
    parser.add_argument(
        "--test-all",
        action="store_true",
        help="すべての方法をテスト"
    )
    
    args = parser.parse_args()
    
    print("\n" + "━"*80)
    print("  🔬 スクレイピング検証")
    print("━"*80)
    print(f"レースID: {args.race}")
    print(f"日時: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    results = []
    
    if args.test_all:
        print("\n📊 すべての方法をテスト中...")
        results.append(test_requests_method(args.race))
        results.append(test_undetected_method(args.race))
        results.append(test_playwright_method(args.race))
        results.append(test_selenium_method(args.race))
    else:
        if args.method == 'requests':
            results.append(test_requests_method(args.race))
        elif args.method == 'undetected':
            results.append(test_undetected_method(args.race))
        elif args.method == 'playwright':
            results.append(test_playwright_method(args.race))
        elif args.method == 'selenium':
            results.append(test_selenium_method(args.race))
    
    # 結果サマリー
    print("\n" + "="*80)
    print("【検証結果サマリー】")
    print("="*80)
    
    for result in results:
        if result.get('success'):
            method = result.get('method', 'Unknown')
            elapsed = result.get('elapsed', 0)
            race_name = result.get('race_name', 'Unknown')
            horse_count = result.get('horse_count', 0)
            
            print(f"\n✅ {method}")
            print(f"   レース名: {race_name}")
            print(f"   馬数: {horse_count}")
            print(f"   処理時間: {elapsed:.2f}秒")
        else:
            method = result.get('method', 'Unknown')
            error = result.get('error', 'Unknown error')
            print(f"\n❌ {method}")
            print(f"   エラー: {error}")
    
    # 推奨方法
    successful = [r for r in results if r.get('success')]
    if successful:
        fastest = min(successful, key=lambda x: x.get('elapsed', float('inf')))
        print(f"\n🏆 推奨方法: {fastest.get('method')}")
        print(f"   理由: 処理時間 {fastest.get('elapsed', 0):.2f}秒で最速")
    else:
        print(f"\n⚠️ すべての方法が失敗しました")
        print(f"💡 ネットワーク接続を確認してください")


if __name__ == "__main__":
    main()
