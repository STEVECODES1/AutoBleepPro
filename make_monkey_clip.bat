@echo off
REM ONE-SHOT monkey clip maker: pass one video file (%1) and it creates + uploads a clip
REM Usage: make_monkey_clip.bat "D:\videos stizz\'!howl' 5-14-26 Stackswopo Stream.ts"

cd D:\AutoBleepPro-git
REM Set profile to monkey (fixed rectangle, shorter clips, app-style framing)
python -X utf8 -c "import json; c=json.load(open('auto_uploader/config.json', encoding='utf8')); c['clips']['profile']='monkey'; json.dump(c, open('auto_uploader/config.json', 'w'), indent=4)"
REM Run one specific file through the pipeline
python -X utf8 auto_uploader/main.py --file "%~1" --only shorts --keep-source
