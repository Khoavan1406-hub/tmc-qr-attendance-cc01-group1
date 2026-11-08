"""Figure 4.18 - Gate Core class diagram, drawn from src/tmc_gate (updated for PR #15: IPassageWindowQuery, PassageTimeoutWorker, 08/11/2026).

Usage: python fig418_classes.py > ../class/figure-4-18-gate-core-classes.drawio
Panel A: application classes, ports owned by Gate Core, and the adapters that implement them.
Panel B: domain entities with the multiplicities enforced by the code and the SQLite schema.
Only names that exist in the code are used.
"""
from xml.sax.saxutils import escape

BLUE = "#1f4e79"
FONT = "html=1;whiteSpace=wrap;fontFamily=Helvetica;"
NAME, MEM, SMALL = 22, 19, 18
LH = 23  # member line height
cells = []
n = [0]
G = {}


def q(s):
    return escape(s, {'"': "&quot;"})


def nid(p="c"):
    n[0] += 1
    return f"{p}{n[0]}"


def v(val, x, y, w, h, style):
    i = nid()
    G[i] = (x, y, w, h)
    cells.append(
        f'<mxCell id="{i}" value="{q(val)}" style="{style}{FONT}" vertex="1" parent="1">'
        f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>'
    )
    return i


def e(src, tgt, style, pts=(), start=None, end=None, labels=()):
    """labels: (text, relative position -1..1, dx, dy) placed along the edge."""
    i = nid("e")
    st = style
    if start:
        x, y, w, h = G[src]
        st += f"exitX={start[0]};exitY={start[1]};exitDx=0;exitDy=0;"
    if end:
        x, y, w, h = G[tgt]
        st += f"entryX={end[0]};entryY={end[1]};entryDx=0;entryDy=0;"
    g = '<mxGeometry relative="1" as="geometry">'
    if pts:
        g += '<Array as="points">' + "".join(f'<mxPoint x="{a}" y="{b}"/>' for a, b in pts) + "</Array>"
    g += "</mxGeometry>"
    cells.append(
        f'<mxCell id="{i}" value="" style="{st}html=1;" edge="1" parent="1" source="{src}" target="{tgt}">{g}</mxCell>'
    )
    for text, pos, dx, dy in labels:
        cells.append(
            f'<mxCell id="{nid("l")}" value="{q(text)}" style="edgeLabel;html=1;fontSize={SMALL};'
            f'fontFamily=Helvetica;labelBackgroundColor=#ffffff;" vertex="1" connectable="0" parent="{i}">'
            f'<mxGeometry x="{pos}" relative="1" as="geometry"><mxPoint x="{dx}" y="{dy}" as="offset"/></mxGeometry></mxCell>'
        )
    return i


CLS = f"rounded=0;fillColor=#ffffff;strokeColor={BLUE};strokeWidth=2;fontColor=#000000;verticalAlign=top;align=left;spacing=0;overflow=fill;"
IFC = "rounded=1;arcSize=6;fillColor=#ffffff;strokeColor=#000000;strokeWidth=1.5;fontColor=#000000;verticalAlign=top;align=left;spacing=0;overflow=fill;"


def box(name, x, y, w, attrs=(), ops=(), stereo=None, iface=False, h=None, bold=True, fs=NAME):
    head = ""
    if stereo:
        head += f'<div style="text-align:center;font-size:{SMALL}px;padding-top:4px">«{stereo}»</div>'
    weight = "bold" if bold else "normal"
    style_i = "font-style:italic;" if iface else ""
    pad = "2px" if stereo else "6px"
    head += f'<div style="text-align:center;font-size:{fs}px;font-weight:{weight};{style_i}padding:{pad} 4px 6px 4px">{name}</div>'
    body = ""
    for part in (attrs, ops):
        if part:
            lines = "<br>".join(part)
            body += (f'<div style="border-top:1px solid #000;font-size:{MEM}px;line-height:{LH}px;'
                     f'padding:4px 8px 5px 8px;text-align:left">{lines}</div>')
    if h is None:
        h = (24 if stereo else 0) + 12 + int(fs * 1.2) + sum(len(p) * LH + 10 for p in (attrs, ops) if p)
    return v(head + body, x, y, w, h, IFC if iface else CLS)


ZONE = "rounded=0;fillColor=#f2f2f2;strokeColor=none;"
ZL = f"text;verticalAlign=top;fontSize={SMALL};fontColor=#555555;fontStyle=1;align=left;"


def zone(x, y, w, h, label):
    v("", x, y, w, h, ZONE)
    v(label, x + 10, y + 4, w - 20, 26, ZL)


FRAME = "rounded=0;fillColor=none;strokeColor=#888888;dashed=1;strokeWidth=1.5;"


def frame(x, y, w, h, label):
    i = v("", x, y, w, h, FRAME)
    v(label, x + 8, y + 3, w - 16, 24, f"text;fontSize={SMALL};fontColor=#555555;fontStyle=2;align=left;verticalAlign=top;")
    return i


USE = "edgeStyle=orthogonalEdgeStyle;rounded=0;endArrow=open;endSize=12;dashed=1;strokeColor=#000000;strokeWidth=1.4;"
REAL = "edgeStyle=orthogonalEdgeStyle;rounded=0;endArrow=block;endFill=0;endSize=14;dashed=1;strokeColor=#000000;strokeWidth=1.4;"
GEN = "edgeStyle=orthogonalEdgeStyle;rounded=0;endArrow=block;endFill=0;endSize=14;strokeColor=#000000;strokeWidth=1.4;"
ASSOC = "edgeStyle=orthogonalEdgeStyle;rounded=0;endArrow=none;strokeColor=#000000;strokeWidth=1.6;"
W = 1120

# ===================================================================== Panel A
AH = 860
zone(0, 0, W, AH, "application/  ·  ports/  ·  adapters/   (M1 Gate Core, with M3 Gate Intervention as a second client)")

i_upd = box("IFactoryUpdates", 20, 44, 250, stereo="interface", iface=True)
i_scan = box("IScanSubmission", 300, 44, 250, stereo="interface", iface=True)
i_int = box("IInterventionRequest", 580, 44, 250, stereo="interface", iface=True)
svc = box("InterventionService", 860, 44, 245, stereo="M3 Gate Intervention")
e(svc, i_int, USE, start=(0, 0.6), end=(1, 0.6))

api = box("GateCoreApi", 300, 150, 250, stereo="interface", iface=True)
for t in (i_upd, i_scan, i_int):
    e(api, t, GEN, start=(0.5, 0), end=(0.5, 1))

fac = box("GateCoreFacade", 20, 250, 245, ops=["submitScan()", "getScanStatus()", "applyEligibilityChanges()", "readCommittedEvents()"])
e(fac, api, REAL, start=(0.5, 0), end=(0, 0.6))

proc = box(
    "GateTransactionProcessor", 300, 250, 500,
    attrs=["OPEN_COMMAND_BUDGET_MS = 700", "ASSISTED_OPEN_TIMEOUT_MS = 3000", "PASSAGE_OBSERVATION_WINDOW_MS = 15000"],
    ops=["processScan(scanId, qrToken, laneId): ScanResult", "onPassage(passageEventId, commandId, laneId)", "onLaneClear(commandId, laneId)",
         "sweepPassageTimeouts(laneIds): int", "requestAssistedOpen(supportCaseId, laneId)", "requestEmergencyRelease(…), …Reset(…)"],
)
e(fac, proc, USE, start=(1, 0.25), end=(0, 0.25 * 0.0 + 0.137))

i_pe = box("IPassageEvents", 840, 250, 265, ops=["onPassage()", "onLaneClear()"], stereo="interface", iface=True)
e(proc, i_pe, REAL, start=(1, 0.2), end=(0, 0.45))

acc = box("AccessDecision", 20, 430, 245, ops=["evaluate(qrToken, laneId)", "  : DecisionResult"])
wrk = box("PassageTimeoutWorker", 840, 430, 265, ops=["run_once(): int  (every 0.25 s)"], stereo="adapter", bold=False, fs=SMALL + 1)
e(wrk, proc, USE, start=(0, 0.5), end=(1, 0.85))

e(proc, acc, USE, start=(0, 0.75), end=(1, 0.5))

# ---- ports owned by Gate Core
PY = 560
RH = 40
def port(nm, x, y, w):
    return box(nm, x, y, w, iface=True, h=34, bold=False, fs=SMALL + 1)
fr_store = frame(20, PY, 545, 30 + 3 * RH + 4, "storage ports (ports/repositories.py)")
for k, nm in enumerate(["IUnitOfWork", "IScanRepository", "IAttendanceRepository", "ISnapshotRepository", "IOutboxRepository", "IPassageWindowQuery"]):
    port(nm, 30 + (k % 2) * 265, PY + 30 + (k // 2) * RH, 258)
fr_ctl = frame(580, PY, 525, 30 + 3 * RH + 4, "controller and cross-cutting ports")
P = {}
for k, nm in enumerate(["IAuditApi", "IHealthApi", "IAuthorizationApi", "IGuardIdentityApi", "IControllerStatusQuery", "ITurnstileControl"]):
    P[nm] = port(nm, 590 + (k % 2) * 256, PY + 30 + (k // 2) * RH, 250)
e(proc, fr_store, USE, start=(0.3, 1), end=(0.75, 0))
e(proc, fr_ctl, USE, start=(0.8, 1), end=(0.3, 0))
e(acc, fr_store, USE, start=(0.5, 1), end=(0.23, 0))

AY = PY + 30 + 3 * RH + 4 + 46
sq = box("SqliteUnitOfWork, Sqlite…Repository ×4<br><span style='font-size:16px'>SqliteScanRepository also implements IPassageWindowQuery</span>", 20, AY, 545, stereo="adapters", bold=False, fs=SMALL + 1, h=86)
sim = box("SimulatedTurnstileAdapter", 580, AY, 258, stereo="adapter", bold=False, fs=SMALL + 1)
fau = box("FaultyTurnstileAdapter", 848, AY, 257, stereo="adapter", bold=False, fs=SMALL + 1)
e(sq, fr_store, REAL, start=(0.5, 0), end=(0.5, 1))
e(sim, P["IControllerStatusQuery"], REAL, start=(0.45, 0), end=(0.45, 1))
e(sim, P["ITurnstileControl"], REAL, start=(0.92, 0), end=(0.2, 1), pts=[(817, AY - 26), (896, AY - 26)])
e(fau, P["ITurnstileControl"], REAL, start=(0.6, 0), end=(0.6, 1))

# ===================================================================== Panel B
BY = AH + 20
C1, C2, C3, CW = 20, 400, 785, 320
GAP = 60

lane = box("Lane", C1, BY + 44, CW, attrs=["laneId: str", "factoryId: str", "direction: Direction", "activeCycleId: str | None"])
cyc = box("GateCycle", C2, BY + 44, CW, attrs=["cycleId: str", "laneId: str", "passageStatus: PassageStatus", "closureStatus: ClosureStatus",
                                                "observationDeadline: datetime", "scanId: str | None", "supportCaseId: str | None",
                                                "laneClearReceived: bool", "closeCommandId: str | None"],
          ops=["is_active_or_unresolved(): bool"])
sup = box("SupportCase", C3, BY + 44, CW, attrs=["supportCaseId: str", "laneId: str", "guardId: str", "isEmergency: bool"])
R2 = BY + 230
scan = box("ScanAttempt", C1, R2, CW, attrs=["scanId: str", "laneId: str", "state: ScanState", "tokenId: str | None",
                                            "employeeId: str | None", "degradedFlag: bool"])
cmd = box("GateCommand", C3, R2, CW, attrs=["commandId: str", "cycleId: str", "commandType: CommandType", "outcome: CommandOutcome",
                                           "issuedAt: datetime | None"])
R3 = BY + 460
tok = box("TokenReservation", C1, R3, CW, attrs=["tokenId: str  {unique per factory}", "factoryId: str", "scanId: str  {unique}", "claimedAt, expiresAt: datetime"])
att = box("AttendanceTransaction", C2, R3, CW, attrs=["transactionId: str", "scanId: str  {unique}", "passageEventId: str  {unique}",
                                                    "employeeId: str", "degradedFlag: bool"])
pas = box("PassageEvent", C3, R3, CW, attrs=["passageEventId: str", "commandId: str", "cycleId: str", "occurredAt: datetime",
                                            "reviewReason: str | None"])
R4 = BY + 660
snap = box("EligibilitySnapshot", C1, R4, CW, attrs=["snapshotVersion: int", "confirmedWatermark: datetime", "verificationKeys: tuple"],
           ops=["getOperatingMode(now)", "isEmployeeEligible(…)"])
v("<b>Notation.</b> Dashed open arrow: «use». Dashed hollow triangle: implements. Solid hollow triangle: extends. "
  "Plain line: association with multiplicities. {unique}: enforced by the data model.",
  C2, R4 + 10, 705, 100, f"text;fontSize={SMALL};fontColor=#333333;align=left;verticalAlign=top;whiteSpace=wrap;")
BH = R4 + 200 - BY
zone_cells = []


def pt(i, s):
    x, y, w, h = G[i]
    return x + w * s[0], y + h * s[1]


def tag(text, x, y):
    v(text, x, y, 50, 22, f"text;fontSize={SMALL};align=left;verticalAlign=middle;spacing=0;labelBackgroundColor=none;")


def end_label(i, s, text, below=False):
    x, y = pt(i, s)
    if s[0] == 1:
        tag(text, x + 5, y + (3 if below else -24))
    elif s[0] == 0:
        tag(text, x - 5 - 9.5 * len(text), y + (3 if below else -24))
    elif s[1] == 1:
        tag(text, x + 6, y + 3)
    else:
        tag(text, x + 6, y - 24)


def mult(a, b, ma, mb, start, end, pts=(), name=None):
    e(a, b, ASSOC, start=start, end=end, pts=pts)
    end_label(a, start, ma)
    end_label(b, end, mb, below=True)


def fy(i, y):
    return (y - G[i][1]) / G[i][3]


YA = G[lane][1] + 56
mult(lane, cyc, "1", "0..1", (1, fy(lane, YA)), (0, fy(cyc, YA)))
mult(sup, cyc, "0..1", "*", (0, fy(sup, YA)), (1, fy(cyc, YA)))
YB = R2 + 32
mult(scan, cyc, "0..1", "0..1", (1, fy(scan, YB)), (0, fy(cyc, YB)))
mult(cyc, cmd, "1", "1..*", (1, fy(cyc, YB)), (0, fy(cmd, YB)))
mult(scan, tok, "1", "0..1", (0.5, 1), (0.5, 0))
mult(pas, att, "1", "0..1", (0, 0.35), (1, 0.35))
mult(cyc, pas, "1", "*", (0.85, 1), (0.15, 0))
xs, ys = pt(scan, (1, 0.85)); xe, ye = pt(att, (0, 0.35))
mult(scan, att, "1", "0..1", (1, 0.85), (0, 0.35), pts=[(C1 + CW + 30, ys), (C1 + CW + 30, ye)])
mult(cmd, pas, "1", "*", (0.75, 1), (0.75, 0))

# grey background for panel B goes first so it stays behind the classes
cells.insert(0, f'<mxCell id="zb" value="" style="{ZONE}" vertex="1" parent="1"><mxGeometry x="0" y="{BY}" width="{W}" height="{BH}" as="geometry"/></mxCell>')
cells.insert(1, f'<mxCell id="zbl" value="domain/  (entities and multiplicities)" style="{ZL}{FONT}" vertex="1" parent="1"><mxGeometry x="10" y="{BY + 4}" width="600" height="26" as="geometry"/></mxCell>')

print('<mxfile host="app.diagrams.net"><diagram name="Figure 4.18" id="fig418"><mxGraphModel dx="1200" dy="1900" grid="0" '
      'gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="0" pageScale="1" pageWidth="1120" '
      'pageHeight="1700" math="0" shadow="0"><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
      + "".join(cells) + "</root></mxGraphModel></diagram></mxfile>")
