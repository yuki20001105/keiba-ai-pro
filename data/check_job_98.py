import urllib.request, json, time

job_id = "98ecb04b"
for i in range(5):
    time.sleep(6)
    resp = urllib.request.urlopen(f"http://localhost:8000/api/scrape/status/{job_id}")
    d = json.loads(resp.read())
    status = d.get("status")
    msg = d.get("progress", {}).get("message", "")
    races = d.get("progress", {}).get("saved_races", 0)
    print(f"[{(i+1)*6}s] status={status} | races={races} | {msg}")
    if status not in ("running", "queued"):
        break
