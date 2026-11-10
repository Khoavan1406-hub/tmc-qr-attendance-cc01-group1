#!/bin/bash
B=http://127.0.0.1:8012
post(){ echo "\$ curl -X POST $1 -d '$3'"; curl -s -X POST $B$1 -H 'content-type: application/json' -d "$2"; echo; echo; }
QR=$(curl -s -X POST $B/api/demo/qr-tokens -H 'content-type: application/json' -d '{"employeeId":"EMP-001","factoryId":"BD"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["qrToken"])')
echo "# QR token issued by the demo issuer for EMP-001 at factory BD -> \$QR"; echo
echo "(a) first scan on LANE-BD-01"
R=$(curl -s -X POST $B/api/gate/scans -H 'content-type: application/json' -d "{\"scanId\":\"S-1\",\"qrToken\":\"$QR\",\"laneId\":\"LANE-BD-01\"}")
echo "\$ curl -X POST /api/gate/scans -d '{\"scanId\":\"S-1\",\"qrToken\":\"\$QR\",\"laneId\":\"LANE-BD-01\"}'"; echo "$R"; echo
C=$(echo "$R" | python3 -c 'import sys,json;print(json.load(sys.stdin)["commandId"])')
echo "(b) same QR token on LANE-BD-02"
post /api/gate/scans "{\"scanId\":\"S-2\",\"qrToken\":\"$QR\",\"laneId\":\"LANE-BD-02\"}" '{"scanId":"S-2","qrToken":"$QR","laneId":"LANE-BD-02"}'
echo "(c) controller sends PASSAGE PE-1 twice, then LANE_CLEAR twice"
for i in 1 2; do post /api/controller/signals/passage "{\"passageEventId\":\"PE-1\",\"commandId\":\"$C\",\"laneId\":\"LANE-BD-01\"}" "{\"passageEventId\":\"PE-1\",\"commandId\":\"$C\",\"laneId\":\"LANE-BD-01\"}"; done
for i in 1 2; do post /api/controller/signals/lane-clear "{\"commandId\":\"$C\",\"laneId\":\"LANE-BD-01\"}" "{\"commandId\":\"$C\",\"laneId\":\"LANE-BD-01\"}"; done
echo "(d) separate sqlite3 connection to factory.sqlite3"
python3 - <<'P'
import sqlite3; c=sqlite3.connect('file:factory.sqlite3?mode=ro',uri=True)
q=["SELECT scan_id, state, reason FROM scan_attempts ORDER BY scan_id",
   "SELECT scan_id, passage_event_id, employee_id, direction FROM attendance_transactions",
   "SELECT command_type, outcome, COUNT(*) FROM gate_commands GROUP BY command_type, outcome",
   "SELECT COUNT(*) FROM passage_events"]
for s in q:
    print("sqlite> "+s+";")
    for r in c.execute(s): print("  "+" | ".join(str(x) for x in r))
P
