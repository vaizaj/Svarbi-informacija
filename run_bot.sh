#!/bin/bash
cd /root/Svarbi-informacija
set -a
source .env
set +a
source venv/bin/activate
python3 "$1" >> "logs/$(basename "$1" .py).log" 2>&1
