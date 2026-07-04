import sqlite3

# pedigree_cache is a separate DB
ped_db = r'keiba\data\pedigree_cache.db'
try:
    ped_con = sqlite3.connect(ped_db)
    ped_cur = ped_con.cursor()
    ped_cur.execute('SELECT COUNT(*) FROM pedigree_cache')
    print('pedigree_cache 件数:', ped_cur.fetchone()[0])
    ped_con.close()
except Exception as e:
    print('pedigree_cache DB エラー:', e)

db = r'keiba\data\keiba_ultimate.db'
con = sqlite3.connect(db)
cur = con.cursor()

cur.execute("SELECT COUNT(DISTINCT horse_id) FROM race_results_ultimate WHERE sire IS NOT NULL AND sire != ''")
print('race_results_ultimate sire入り馬数:', cur.fetchone()[0])

cur.execute('SELECT COUNT(DISTINCT horse_id) FROM race_results_ultimate')
total = cur.fetchone()[0]
print('race_results_ultimate 総ユニーク馬数:', total)

cur.execute('SELECT COUNT(*) FROM race_results_ultimate')
print('race_results_ultimate 総行数:', cur.fetchone()[0])

# カラム一覧確認
cur.execute('PRAGMA table_info(race_results_ultimate)')
cols = [r[1] for r in cur.fetchall()]
print('sire in cols:', 'sire' in cols)
print('damsire in cols:', 'damsire' in cols)
print('jockey_id in cols:', 'jockey_id' in cols)

# 直近レース（2026年）の馬の血統カバレッジ
cur.execute("SELECT COUNT(DISTINCT horse_id) FROM race_results_ultimate WHERE race_id >= '202600000000'")
horses_2026 = cur.fetchone()[0]
cur.execute("SELECT COUNT(DISTINCT horse_id) FROM race_results_ultimate WHERE race_id >= '202600000000' AND sire IS NOT NULL AND sire != ''")
horses_2026_sire = cur.fetchone()[0]
print(f'2026年以降の馬: {horses_2026}頭 (sireあり: {horses_2026_sire}頭, {horses_2026_sire/max(horses_2026,1)*100:.1f}%)')

# shutuba_entries (出馬表) テーブルがあるか確認
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cur.fetchall()]
print('テーブル一覧:', tables)

con.close()
