"""Run the three Chapter 9 demos without bash (works in PowerShell, cmd, macOS, Linux).

Start the server first, from the repository root:
    PowerShell:  $env:TMC_GATE_DB="factory.sqlite3"; uvicorn tmc_gate.api.app:app --app-dir src --port 8000
    bash:        TMC_GATE_DB=factory.sqlite3 uvicorn tmc_gate.api.app:app --app-dir src --port 8000
Then, from the same folder (it reads factory.sqlite3 in the current folder):
    python report/evidence/run_demo.py flow-a     # Figure 9.5
    python report/evidence/run_demo.py demo2      # Figure 9.6
    python report/evidence/run_demo.py demo3      # Figure 9.7
Use a fresh factory.sqlite3 for each demo. Only the standard library is used.
"""

import json
import sqlite3
import sys
import urllib.error
import urllib.request

B = "http://127.0.0.1:8000"
GUARD = ("X-Guard-Session", "demo-session-guard-bd-01")


def call(path, body, header=None):
    req = urllib.request.Request(B + path, data=json.dumps(body).encode(), method="POST")
    req.add_header("content-type", "application/json")
    if header:
        req.add_header(*header)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def fmt(obj):
    return json.dumps(obj, separators=(", ", ": "))


def show(path, shown, body, header=None):
    h = f" -H '{header[0]}: {header[1]}'" if header else ""
    print(f"$ curl -X POST {path}{h} -d '{shown}'")
    code, out = call(path, body, header)
    if code != 200:
        print(f"HTTP {code}")
    print(fmt(out))
    print()
    return out


def qr(emp):
    return call("/api/demo/qr-tokens", {"employeeId": emp, "factoryId": "BD"})[1]["qrToken"]


def sql(*queries):
    c = sqlite3.connect("file:factory.sqlite3?mode=ro", uri=True)
    for q in queries:
        print("sqlite> " + q + ";")
        for row in c.execute(q):
            print("  " + " | ".join(str(x) for x in row))
    c.close()


def scan_shown(sid, var, lane):
    return '{"scanId":"%s","qrToken":"$%s","laneId":"%s"}' % (sid, var, lane)


def flow_a():
    token = qr("EMP-001")
    print("# QR token issued by the demo issuer for EMP-001 at factory BD -> $QR\n")
    print("(a) first scan on LANE-BD-01")
    s1 = show("/api/gate/scans", scan_shown("S-1", "QR", "LANE-BD-01"),
              {"scanId": "S-1", "qrToken": token, "laneId": "LANE-BD-01"})
    cmd = s1["commandId"]
    print("(b) same QR token on LANE-BD-02")
    show("/api/gate/scans", scan_shown("S-2", "QR", "LANE-BD-02"),
         {"scanId": "S-2", "qrToken": token, "laneId": "LANE-BD-02"})
    print("(c) controller sends PASSAGE PE-1 twice, then LANE_CLEAR twice")
    p = {"passageEventId": "PE-1", "commandId": cmd, "laneId": "LANE-BD-01"}
    lc = {"commandId": cmd, "laneId": "LANE-BD-01"}
    for _ in range(2):
        show("/api/controller/signals/passage", json.dumps(p, separators=(",", ":")), p)
    for _ in range(2):
        show("/api/controller/signals/lane-clear", json.dumps(lc, separators=(",", ":")), lc)
    print("(d) separate sqlite3 connection to factory.sqlite3")
    sql("SELECT scan_id, state, reason FROM scan_attempts ORDER BY scan_id",
        "SELECT scan_id, passage_event_id, employee_id, direction FROM attendance_transactions",
        "SELECT command_type, outcome, COUNT(*) FROM gate_commands GROUP BY command_type, outcome",
        "SELECT COUNT(*) FROM passage_events")


def demo2():
    print("(a) access data 300 s old -> DEGRADED, the scan is still accepted but flagged")
    show("/api/demo/watermark-age", '{"watermarkAgeSeconds":300}', {"watermarkAgeSeconds": 300})
    s10 = show("/api/gate/scans", scan_shown("S-10", "QR_EMP001", "LANE-BD-01"),
               {"scanId": "S-10", "qrToken": qr("EMP-001"), "laneId": "LANE-BD-01"})
    sig = {"commandId": s10["commandId"], "laneId": "LANE-BD-01"}
    call("/api/controller/signals/passage", {**sig, "passageEventId": "PE-10"})
    call("/api/controller/signals/lane-clear", sig)
    print("# (PASSAGE PE-10 and LANE_CLEAR sent for S-10, so LANE-BD-01 is free again)\n")
    print("(b) access data 1800 s old -> CANNOT_PROCESS, no automatic scan")
    show("/api/demo/watermark-age", '{"watermarkAgeSeconds":1800}', {"watermarkAgeSeconds": 1800})
    show("/api/gate/scans", scan_shown("S-11", "QR_EMP002", "LANE-BD-01"),
         {"scanId": "S-11", "qrToken": qr("EMP-002"), "laneId": "LANE-BD-01"})
    print("(c) data fresh again, but the controller reply times out and a status query cannot tell")
    show("/api/demo/watermark-age", '{"watermarkAgeSeconds":0}', {"watermarkAgeSeconds": 0})
    mode = {"openMode": "TIMEOUT", "queryStatus": "UNDETERMINED"}
    show("/api/demo/controller-mode", '{"openMode":"TIMEOUT","queryStatus":"UNDETERMINED"}', mode)
    show("/api/gate/scans", scan_shown("S-12", "QR_EMP002_new", "LANE-BD-02"),
         {"scanId": "S-12", "qrToken": qr("EMP-002"), "laneId": "LANE-BD-02"})
    show("/api/gate/scans", scan_shown("S-13", "QR_EMP001_new", "LANE-BD-02"),
         {"scanId": "S-13", "qrToken": qr("EMP-001"), "laneId": "LANE-BD-02"})
    print("(d) separate sqlite3 connection to factory.sqlite3")
    sql("SELECT scan_id, state, degraded_flag, reason FROM scan_attempts ORDER BY scan_id",
        "SELECT scan_id, degraded_flag FROM attendance_transactions",
        "SELECT scan_id, command_type, outcome FROM gate_commands WHERE scan_id = 'S-12'",
        "SELECT lane_id, active_cycle_id IS NOT NULL AS held FROM lanes ORDER BY lane_id")


def demo3():
    case = {"supportCaseId": "case-001", "employeeId": "EMP-001", "laneId": "LANE-BD-02",
            "reason": "phone battery empty"}
    case_shown = json.dumps(case, separators=(",", ":"))
    print("(a) assisted opening without a guard session is refused")
    show("/api/interventions/assisted-open", case_shown, case)
    print("(b) the same request with a guard session, sent twice")
    first = show("/api/interventions/assisted-open", case_shown, case, GUARD)
    show("/api/interventions/assisted-open", case_shown, case, GUARD)
    a = first["commandId"]
    print("(c) the employee walks through at once: PASSAGE, then LANE_CLEAR, for the assisted command")
    p = {"passageEventId": "PE-30", "commandId": a, "laneId": "LANE-BD-02"}
    lc = {"commandId": a, "laneId": "LANE-BD-02"}
    show("/api/controller/signals/passage", json.dumps(p, separators=(",", ":")), p)
    show("/api/controller/signals/lane-clear", json.dumps(lc, separators=(",", ":")), lc)
    print("(d) emergency release on LANE-BD-01, then a QR scan on that lane")
    emg = {"supportCaseId": "emg-001", "laneId": "LANE-BD-01", "reason": "fire alarm"}
    r = show("/api/interventions/emergency-release", json.dumps(emg, separators=(",", ":")), emg, GUARD)
    c = r["commandId"]
    show("/api/gate/scans", scan_shown("S-20", "QR_EMP002", "LANE-BD-01"),
         {"scanId": "S-20", "qrToken": qr("EMP-002"), "laneId": "LANE-BD-01"})
    print("(e) reset, then LANE_CLEAR closes the gate and the lane takes scans again")
    rst = {"supportCaseId": "emg-001", "laneId": "LANE-BD-01"}
    show("/api/interventions/emergency-reset", json.dumps(rst, separators=(",", ":")), rst, GUARD)
    lc = {"commandId": c, "laneId": "LANE-BD-01"}
    show("/api/controller/signals/lane-clear", json.dumps(lc, separators=(",", ":")), lc)
    show("/api/gate/scans", scan_shown("S-21", "QR_EMP002_new", "LANE-BD-01"),
         {"scanId": "S-21", "qrToken": qr("EMP-002"), "laneId": "LANE-BD-01"})
    print("(f) separate sqlite3 connection to factory.sqlite3")
    sql("SELECT support_case_id, lane_id FROM support_cases ORDER BY support_case_id",
        "SELECT support_case_id, command_type, outcome FROM gate_commands"
        " WHERE support_case_id IS NOT NULL ORDER BY issued_at",
        "SELECT passage_event_id, lane_id, review_reason FROM passage_events",
        "SELECT COUNT(*) AS attendance_records FROM attendance_transactions")


DEMOS = {"flow-a": flow_a, "demo2": demo2, "demo3": demo3}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in DEMOS:
        sys.exit("usage: python report/evidence/run_demo.py flow-a|demo2|demo3")
    print("# Server: TMC_GATE_DB=factory.sqlite3 uvicorn tmc_gate.api.app:app --app-dir src --port 8000"
          " (demo mode, simulated controller)")
    if sys.argv[1] != "demo2":
        # The server treats its access data as fresh only at start-up (no Site Sync in the prototype);
        # refresh it so a run started more than 60 s after the server does not come out DEGRADED.
        call("/api/demo/watermark-age", {"watermarkAgeSeconds": 0})
    DEMOS[sys.argv[1]]()
