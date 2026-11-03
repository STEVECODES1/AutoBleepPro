@echo off
REM ONE-SHOT gta clip maker: pass one video file (%1) — full-bleed center crop, storyline clips
REM Usage: make_gta_clip.bat "D:\videos stizz\Stackswopo - THUGGIN ON A STAURDAY - 05-09-26 (FULL STREAM).ts"

cd D:\AutoBleepPro-git
python -X utf8 -c "import json; c=json.load(open('auto_uploader/config.json', encoding='utf8')); c['clips']['profile']='gta'; json.dump(c, open('auto_uploader/config.json', 'w'), indent=4)"
python -X utf8 auto_uploader/main.py --file "%~1" --only shorts --keep-source
