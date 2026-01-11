import requests
from bs4 import BeautifulSoup

# 以前成功したrace_id
race_id = "202606010411"
url = f"https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"

headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

print(f"Testing race_id: {race_id}")
print(f"URL: {url}\n")

response = requests.get(url, headers=headers, timeout=15)
print(f"Status: {response.status_code}")
print(f"Content-Length: {len(response.content)}")

if response.status_code == 200:
    soup = BeautifulSoup(response.text, 'html.parser')
    race_name = soup.select_one('h1.RaceName')
    if race_name:
        print(f"✓ RaceName: {race_name.get_text(strip=True)}")
        print("\n✓ SUCCESS! This race_id works!")
    else:
        print("✗ RaceName not found")
        print(f"Page content first 200 chars: {response.text[:200]}")
else:
    print(f"⚠ Status {response.status_code} - Race not found or invalid")
