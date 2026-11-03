@echo off
REM Runs AutoBleepPro pipeline for GTA profile (full streams, full-bleed center crop, storyline clips)
REM Place a source video as %1 (optional); runs main.py --watch with profile set to gta

cd D:\AutoBleepPro-git
REM Apply gta profile to live config
python -X utf8 -c "import json; c=json.load(open('auto_uploader/config.json', encoding='utf8')); c['clips']['profile']='gta'; json.dump(c, open('auto_uploader/config.json', 'w'), indent=4)"
REM Start the unattended watcher/uploader
python -X utf8 main.py --watch
