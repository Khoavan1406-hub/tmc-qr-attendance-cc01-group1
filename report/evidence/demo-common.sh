# Shared helpers for the demo scripts. Server: PYTHONPATH=src TMC_GATE_DB=factory.sqlite3 uvicorn tmc_gate.api.app:app --port 8000
B=http://127.0.0.1:8000
# show <path> <shown body> <real body> [header]
show(){ if [ -n "$4" ]; then echo "\$ curl -X POST $1 -H '$4' -d '$2'"; out=$(curl -s -w '\n%{http_code}' -X POST $B$1 -H 'content-type: application/json' -H "$4" -d "$3"); else echo "\$ curl -X POST $1 -d '$2'"; out=$(curl -s -w '\n%{http_code}' -X POST $B$1 -H 'content-type: application/json' -d "$3"); fi; code=${out##*$'\n'}; [ "$code" != 200 ] && echo "HTTP $code"; echo "${out%$'\n'*}"; echo; }
qr(){ curl -s -X POST $B/api/demo/qr-tokens -H 'content-type: application/json' -d "{\"employeeId\":\"$1\",\"factoryId\":\"BD\"}" | python3 -c 'import sys,json;print(json.load(sys.stdin)["qrToken"])'; }
sql(){ python3 - "$@" <<'P'
import sqlite3,sys; c=sqlite3.connect('file:factory.sqlite3?mode=ro',uri=True)
for s in sys.argv[1:]:
    print("sqlite> "+s+";")
    for r in c.execute(s): print("  "+" | ".join(str(x) for x in r))
P
}
