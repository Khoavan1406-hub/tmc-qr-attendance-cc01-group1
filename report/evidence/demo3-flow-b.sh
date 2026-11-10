#!/bin/bash
# Demo 3: Flow B in the factory application. Start the server first:
#   PYTHONPATH=src TMC_GATE_DB=factory.sqlite3 uvicorn tmc_gate.api.app:app --port 8000
. "$(dirname "$0")/demo-common.sh"
G='X-Guard-Session: demo-session-guard-bd-01'
echo "(a) assisted opening without a guard session is refused"
show /api/interventions/assisted-open '{"supportCaseId":"case-001","employeeId":"EMP-001","laneId":"LANE-BD-02","reason":"phone battery empty"}' '{"supportCaseId":"case-001","employeeId":"EMP-001","laneId":"LANE-BD-02","reason":"phone battery empty"}'
echo "(b) the same request with a guard session, sent twice"
for i in 1 2; do show /api/interventions/assisted-open '{"supportCaseId":"case-001","employeeId":"EMP-001","laneId":"LANE-BD-02","reason":"phone battery empty"}' '{"supportCaseId":"case-001","employeeId":"EMP-001","laneId":"LANE-BD-02","reason":"phone battery empty"}' "$G"; done
A=$(python3 -c "import sqlite3;print(sqlite3.connect('file:factory.sqlite3?mode=ro',uri=True).execute(\"SELECT command_id FROM gate_commands WHERE support_case_id='case-001'\").fetchone()[0])")
echo "(c) the employee walks through at once: PASSAGE, then LANE_CLEAR, for the assisted command"
show /api/controller/signals/passage "{\"passageEventId\":\"PE-30\",\"commandId\":\"$A\",\"laneId\":\"LANE-BD-02\"}" "{\"passageEventId\":\"PE-30\",\"commandId\":\"$A\",\"laneId\":\"LANE-BD-02\"}"
show /api/controller/signals/lane-clear "{\"commandId\":\"$A\",\"laneId\":\"LANE-BD-02\"}" "{\"commandId\":\"$A\",\"laneId\":\"LANE-BD-02\"}"
echo "(d) emergency release on LANE-BD-01, then a QR scan on that lane"
R=$(curl -s -X POST $B/api/interventions/emergency-release -H 'content-type: application/json' -H "$G" -d '{"supportCaseId":"emg-001","laneId":"LANE-BD-01","reason":"fire alarm"}')
echo "\$ curl -X POST /api/interventions/emergency-release -H '$G' -d '{\"supportCaseId\":\"emg-001\",\"laneId\":\"LANE-BD-01\",\"reason\":\"fire alarm\"}'"; echo "$R" | fmt_json; echo
C=$(echo "$R" | python3 -c 'import sys,json;print(json.load(sys.stdin)["commandId"])')
show /api/gate/scans '{"scanId":"S-20","qrToken":"$QR_EMP002","laneId":"LANE-BD-01"}' "{\"scanId\":\"S-20\",\"qrToken\":\"$(qr EMP-002)\",\"laneId\":\"LANE-BD-01\"}"
echo "(e) reset, then LANE_CLEAR closes the gate and the lane takes scans again"
show /api/interventions/emergency-reset '{"supportCaseId":"emg-001","laneId":"LANE-BD-01"}' '{"supportCaseId":"emg-001","laneId":"LANE-BD-01"}' "$G"
show /api/controller/signals/lane-clear "{\"commandId\":\"$C\",\"laneId\":\"LANE-BD-01\"}" "{\"commandId\":\"$C\",\"laneId\":\"LANE-BD-01\"}"
show /api/gate/scans '{"scanId":"S-21","qrToken":"$QR_EMP002_new","laneId":"LANE-BD-01"}' "{\"scanId\":\"S-21\",\"qrToken\":\"$(qr EMP-002)\",\"laneId\":\"LANE-BD-01\"}"
echo "(f) separate sqlite3 connection to factory.sqlite3"
sql "SELECT support_case_id, lane_id FROM support_cases ORDER BY support_case_id" \
    "SELECT support_case_id, command_type, outcome FROM gate_commands WHERE support_case_id IS NOT NULL ORDER BY issued_at" \
    "SELECT passage_event_id, lane_id, review_reason FROM passage_events" \
    "SELECT COUNT(*) AS attendance_records FROM attendance_transactions"
