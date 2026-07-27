#!/bin/bash
cd "/Users/wizkid/Documents/Outreach Project Py V8/paris_app"
.venv/bin/python -X faulthandler -c 'import app.server' > raw_diagnostics.log 2>&1 &
PID=$!
sleep 2
kill -ABRT $PID
wait $PID 2>/dev/null
sleep 1
rm -f ../app_with_diagnostics.zip
zip -r ../app_with_diagnostics.zip . -x ".venv/*" -x "*/__pycache__/*" -x ".git/*" -q
