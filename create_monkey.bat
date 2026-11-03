@echo off
REM Runs AutoBleepPro pipeline for MONKEY profile (calls, short clips, fixed-rectangle crop)
REM Place a source video as %1 (optional); runs main.py --watch with profile set to monkey

cd D:\AutoBleepPro-git
REM Apply monkey profile to live config
python -X utf8 -c "import json; c=json.load(open('auto_uploader/config.json', encoding='utf8')); c['clips']['profile']='monkey'; json.dump(c, open('auto_uploader/config.json', 'w'), indent=4)"
REM Start the unattended watcher/uploader
python -X utf8 main.py --watch
