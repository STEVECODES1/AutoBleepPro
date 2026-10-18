"""What failed, what is waiting, what went up - in one look.

    python -X utf8 tools/health_check.py [HOURS] [--yt]

HOURS (default 24): clip-queue jobs updated in that window, by platform and
state, then every job that is not done with its reason ("held: ..." is a
guard working, "daily cap" is a slot it is waiting for). --yt adds the
newest five uploads on each of the four channels (about 12 quota units).
"""
import json, os, sys, time, collections
sys.path.insert(0, r'D:\AutoBleepPro-git\tools')
sys.path.insert(0, r'D:\AutoBleepPro-git\auto_uploader')
os.chdir(r'D:\AutoBleepPro-git\auto_uploader')
HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 24
now = time.time()
d = json.load(open('clip_jobs.json', encoding='utf-8'))
jobs = d['jobs'] if isinstance(d, dict) and 'jobs' in d else d
items = list(jobs.values()) if isinstance(jobs, dict) else jobs
recent = [j for j in items if now - float(j.get('updated_at') or 0) < HOURS * 3600]
c = collections.Counter((j['platform'], j['state']) for j in recent)
print(f'JOBS last {HOURS:g}h:')
for (p, s), n in sorted(c.items()):
    print(f'  {p:16} {s:12} {n}')
print('NOT DONE:')
for j in sorted(recent, key=lambda j: j.get('updated_at', 0)):
    if j['state'] != 'done':
        print(' ', time.strftime('%m-%d %H:%M', time.localtime(j['updated_at'])), j['platform'], j['state'],
              ascii(os.path.basename(j.get('clip_path', '')))[:58], '|', ascii(j.get('last_error', ''))[:170])
if '--yt' in sys.argv:
    from rumble_cta_backfill import service
    for name, tok in [('wopovod', 'youtube_token.json'), ('games', 'youtube_games_token.json'),
                      ('shorts', 'youtube_shorts_token.json'), ('10k', 'youtube_compilation_token.json')]:
        try:
            svc = service(tok)
            ch = svc.channels().list(part='contentDetails', mine=True).execute()['items'][0]
            pl = ch['contentDetails']['relatedPlaylists']['uploads']
            page = svc.playlistItems().list(part='contentDetails', playlistId=pl, maxResults=5).execute()
            ids = [i['contentDetails']['videoId'] for i in page.get('items', [])]
            vids = svc.videos().list(part='snippet,status,statistics', id=','.join(ids)).execute().get('items', [])
            print(f'== {name}')
            for v in vids:
                print('  ', v['snippet']['publishedAt'][:16], v['id'], v['status']['privacyStatus'],
                      v['status'].get('uploadStatus'), v['statistics'].get('viewCount'),
                      ascii(v['snippet']['title'])[:80])
        except Exception as e:
            print(name, 'ERR', ascii(str(e))[:200])
