import urllib.request, json, time

for i in range(20):
    time.sleep(8)
    resp = urllib.request.urlopen("http://localhost:8000/api/scrape/status/c9c5b367")
    d = json.loads(resp.read())
    status = d.get("status")
    msg = d.get("progress", {}).get("message", "")
    print(f"[{(i+1)*8}s] status={status} | {msg}")
    if status != "running":
        break
