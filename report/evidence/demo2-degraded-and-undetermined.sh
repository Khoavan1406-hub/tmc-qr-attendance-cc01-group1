#!/bin/bash
# Demo 2: operating modes and an uncertain opening. Start the server first:
#   PYTHONPATH=src TMC_GATE_DB=factory.sqlite3 uvicorn tmc_gate.api.app:app --port 8000
. "$(dirname "$0")/demo-common.sh"
echo "(a) access data 300 s old -> DEGRADED, the scan is still accepted but flagged"
show /api/demo/watermark-age '{"watermarkAgeSeconds":300}' '{"watermarkAgeSeconds":300}'
Q=$(qr EMP-001)
R=$(curl -s -X POST $B/api/gate/scans -H 'content-type: application/json' -d "{\"scanId\":\"S-10\",\"qrToken\":\"$Q\",\"laneId\":\"LANE-BD-01\"}")
echo "\$ curl -X POST /api/gate/scans -d '{\"scanId\":\"S-10\",\"qrToken\":\"\$QR_EMP001\",\"laneId\":\"LANE-BD-01\"}'"; echo "$R" | fmt_json; echo
C=$(echo "$R" | python3 -c 'import sys,json;print(json.load(sys.stdin)["commandId"])')
curl -s -o /dev/null -X POST $B/api/controller/signals/passage -H 'content-type: application/json' -d "{\"passageEventId\":\"PE-10\",\"commandId\":\"$C\",\"laneId\":\"LANE-BD-01\"}"
curl -s -o /dev/null -X POST $B/api/controller/signals/lane-clear -H 'content-type: application/json' -d "{\"commandId\":\"$C\",\"laneId\":\"LANE-BD-01\"}"
echo "# (PASSAGE PE-10 and LANE_CLEAR sent for S-10, so LANE-BD-01 is free again)"; echo
echo "(b) access data 1800 s old -> CANNOT_PROCESS, no automatic scan"
show /api/demo/watermark-age '{"watermarkAgeSeconds":1800}' '{"watermarkAgeSeconds":1800}'
Q=$(qr EMP-002)
show /api/gate/scans '{"scanId":"S-11","qrToken":"$QR_EMP002","laneId":"LANE-BD-01"}' "{\"scanId\":\"S-11\",\"qrToken\":\"$Q\",\"laneId\":\"LANE-BD-01\"}"
echo "(c) data fresh again, but the controller reply times out and a status query cannot tell"
show /api/demo/watermark-age '{"watermarkAgeSeconds":0}' '{"watermarkAgeSeconds":0}'
show /api/demo/controller-mode '{"openMode":"TIMEOUT","queryStatus":"UNDETERMINED"}' '{"openMode":"TIMEOUT","queryStatus":"UNDETERMINED"}'
show /api/gate/scans '{"scanId":"S-12","qrToken":"$QR_EMP002_new","laneId":"LANE-BD-02"}' "{\"scanId\":\"S-12\",\"qrToken\":\"$(qr EMP-002)\",\"laneId\":\"LANE-BD-02\"}"
show /api/gate/scans '{"scanId":"S-13","qrToken":"$QR_EMP001_new","laneId":"LANE-BD-02"}' "{\"scanId\":\"S-13\",\"qrToken\":\"$(qr EMP-001)\",\"laneId\":\"LANE-BD-02\"}"
echo "(d) separate sqlite3 connection to factory.sqlite3"
sql "SELECT scan_id, state, degraded_flag, reason FROM scan_attempts ORDER BY scan_id" \
    "SELECT scan_id, degraded_flag FROM attendance_transactions" \
    "SELECT scan_id, command_type, outcome FROM gate_commands WHERE scan_id = 'S-12'" \
    "SELECT lane_id, active_cycle_id IS NOT NULL AS held FROM lanes ORDER BY lane_id"
