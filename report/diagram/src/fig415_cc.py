"""Figure 4.15 C&C view - compact landscape layout (v3, 02/11/2026).
Canvas ~1010 wide -> 450 pt: 18 px ~ 8.0 pt, 17 px ~ 7.6 pt, 15 px ~ 6.7 pt."""
from xml.sax.saxutils import escape
import sys
cells=[]; n=[0]; G={}
FONT="html=1;whiteSpace=wrap;fontFamily=Helvetica;"
BLUE="#1f4e79"
def q(s): return escape(s,{'"':"&quot;"})
def nid(p='c'):
    n[0]+=1; return f"{p}{n[0]}"
def v(val,x,y,w,h,style):
    i=nid(); G[i]=(x,y,w,h)
    cells.append(f'<mxCell id="{i}" value="{q(val)}" style="{style}{FONT}" vertex="1" parent="1"><mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')
    return i
def e(src,tgt,style,pts=(),start=None,end=None):
    i=nid('e'); st=style
    if start:
        x,y,w,h=G[src]; st+=f"exitX={(start[0]-x)/w:.4f};exitY={(start[1]-y)/h:.4f};exitDx=0;exitDy=0;"
    if end:
        x,y,w,h=G[tgt]; st+=f"entryX={(end[0]-x)/w:.4f};entryY={(end[1]-y)/h:.4f};entryDx=0;entryDy=0;"
    g='<mxGeometry relative="1" as="geometry">'
    if pts: g+='<Array as="points">'+''.join(f'<mxPoint x="{a}" y="{b}"/>' for a,b in pts)+'</Array>'
    g+='</mxGeometry>'
    cells.append(f'<mxCell id="{i}" value="" style="{st}html=1;" edge="1" parent="1" source="{src}" target="{tgt}">{g}</mxCell>')
ZONE="rounded=0;fillColor=#f2f2f2;strokeColor=none;"
ZL="text;verticalAlign=top;fontSize=15;fontColor=#666666;fontStyle=1;"
def zone(x,y,w,h,label,lx=None,lw=300):
    v("",x,y,w,h,ZONE)
    if label: v(label,lx if lx is not None else x+8,y+3,lw,22,ZL+"align=left;")
NAME=18; TAG=14; ILAB=17; SMALL=15
COMP=f"rounded=0;fillColor=#ffffff;strokeColor={BLUE};strokeWidth=2;fontColor=#000000;fontSize={NAME};verticalAlign=middle;spacingTop=6;"
def comp(name,mod,x,y,w,h=50):
    mod=None  # lookup codes (M1-M13) are kept in tables, not in the figure
    lab=f'<b>{name}</b>'+(f'&nbsp;&nbsp;<font style="font-size:{TAG}px;color:#555555">{mod}</font>' if mod else "")
    i=v(lab,x,y,w,h,COMP)
    v("",x+w-19,y+4,12,14,f"shape=component;jettyWidth=5;jettyHeight=2;fillColor=#ffffff;strokeColor={BLUE};strokeWidth=1;")
    return i
def extsys(name,sub,x,y,w,h=56):
    lab=f'<font style="font-size:{TAG}px">«external system»</font><br><b>{name}</b>'+(f'<br><font style="font-size:{TAG}px;color:#555555">{sub}</font>' if sub else "")
    return v(lab,x,y,w,h,COMP+"dashed=1;strokeColor=#666666;spacingTop=0;")
BALL="ellipse;aspect=fixed;fillColor=#ffffff;strokeColor=#000000;strokeWidth=1.6;"
STEM="endArrow=none;strokeColor=#000000;strokeWidth=1.6;edgeStyle=none;"
def ball(cx,cy,label,lx,ly,lw,al,owner,end=None,lh=20):
    b=v("",cx-8,cy-8,16,16,BALL)
    v(label,lx,ly,lw,lh,f"text;fontSize={ILAB};align={al};verticalAlign=middle;fontStyle=2;")
    e(b,owner,STEM,end=end)
    return b
REQ="edgeStyle=orthogonalEdgeStyle;rounded=0;endArrow=halfCircle;endFill=0;endSize=7;strokeColor=#000000;strokeWidth=1.4;jumpStyle=arc;jumpSize=10;"
ASY="edgeStyle=orthogonalEdgeStyle;rounded=0;endArrow=open;endSize=12;strokeColor=#000000;strokeWidth=1.4;jumpStyle=arc;jumpSize=10;"
DSH=ASY+"dashed=1;"
TXT=f"text;fontSize={SMALL};fontColor=#333333;verticalAlign=middle;"

# ============ zones
zone(20,75,985,215,"FACTORY APPLICATION")
zone(290,300,370,130,"")
zone(20,440,985,320,"CENTRAL APPLICATION",lx=28)

# ============ top clients
sc=comp("Station Client","M13",190,12,220,46)
bp=comp("Back-office Portal","M13",470,12,240,46)

# ============ factory application
gc=comp("Gate Core","M1",30,115,220)
gi=comp("Gate Intervention","M3",470,115,240)
mof=comp("Monitoring (factory)","M12",750,115,220)
ti=comp("Turnstile Integration","M2",30,215,220)
ssf=comp("Site Sync (factory)","M9",290,215,370)

# ============ band: external controller, broker, guard display
tc=extsys("Turnstile controller","existing system",30,316,220,64)
QS=f"rounded=0;fillColor=#ffffff;strokeColor=#333333;fontSize={SMALL};"
q1=v("Gate Event<br>Feed",300,338,105,48,QS)
q2=v("Access Change<br>Feed",420,338,110,48,QS)
q3=v("Resolution<br>Feed",545,338,105,48,QS)
gsd=comp("Guard Status Display","M13",730,318,240)

# ============ central application
acc=comp("Access &amp; Configuration","M4",30,480,262)
ssc=comp("Site Sync (central)","M9",320,480,340)
iac=comp("Identity &amp; Access Control","M10",690,480,280)
aud=comp("Audit","M11",30,580,262)
att=comp("Attendance","M5",320,580,280)
rep=comp("Reporting","M6",690,580,280)
ph=comp("Payroll Handover","M7",320,685,280)
hri=comp("HR/Payroll Integration","M8",690,685,280)

# ============ bottom row (outside the applications)
ep=comp("Employee Portal","M13",30,782,220,46)
moc=comp("Monitoring (central)","M12",290,782,310,46)
hr=extsys("HR/Payroll System","existing system",740,776,232,64)

# ============ provided interfaces
b_gca=ball(300,140,"GateCoreApi",312,100,140,"left",gc)
b_gia=ball(590,92,"GateInterventionApi",602,80,190,"left",gi)
b_itc=ball(120,188,"«port»<br>ITurnstileControl",132,170,150,"left",ti,end=(120,215),lh=40)
b_hf=ball(860,190,"HealthApi",872,180,100,"left",mof)
b_auth=ball(705,462,"AuthorizationApi",717,447,150,"left",iac,end=(705,480))
b_acc=ball(270,505,"AccessConfigApi",28,530,230,"right",acc) if False else None
b_acc=ball(306,505,"",0,0,1,"left",acc)
v("AccessConfigApi",140,534,150,20,f"text;fontSize={ILAB};align=right;verticalAlign=middle;fontStyle=2;")
b_att=ball(640,605,"AttendanceApi",652,636,150,"left",att)
b_aud=ball(140,652,"AuditApi",152,642,90,"left",aud,end=(140,630))
b_rep=ball(830,560,"ReportApi",842,550,100,"left",rep)
b_ph=ball(305,710,"PayrollHandoverApi",100,700,190,"right",ph)
b_ips=ball(645,710,"«port» IPayrollSubmission",535,738,220,"center",hri)
b_hc=ball(632,805,"HealthApi",644,795,90,"left",moc)

# ============ required interfaces (synchronous calls)
e(sc,b_gca,REQ,start=(300,58))
e(gi,b_gca,REQ,start=(470,140))
e(ssf,b_gca,REQ,start=(300,215))
e(gc,b_itc,REQ,start=(120,165))
e(gsd,b_hf,REQ,start=(860,318))
e(bp,b_gia,REQ,start=(590,58))
e(gi,b_auth,REQ,start=(705,165))
e(ssc,b_acc,REQ,start=(320,505))
e(att,b_acc,REQ,start=(320,620),pts=[(306,620)])
e(ssc,b_att,REQ,start=(640,530))
e(rep,b_att,REQ,start=(690,605))
e(ph,b_att,REQ,start=(580,685),pts=[(580,660),(640,660)])
e(rep,b_aud,REQ,start=(900,630),pts=[(900,668),(140,668)])
e(ph,b_ips,REQ,start=(600,710))

# ============ asynchronous transfer
e(ssf,q1,ASY,start=(352,265),end=(352,338))
e(q1,ssc,ASY,start=(352,386),end=(352,480))
e(ssc,q2,ASY,start=(475,480),end=(475,386))
e(q2,ssf,ASY,start=(475,338),end=(475,265))
e(ssc,q3,ASY,start=(597,480),end=(597,386))
e(q3,ssf,ASY,start=(597,338),end=(597,265))
e(mof,moc,ASY,start=(970,140),end=(445,828),pts=[(984,140),(984,850),(445,850)])
v("status and alerts (asynchronous)",610,852,385,20,TXT+"align=right;")

# ============ dashed: external calls, portal access
e(ti,tc,DSH,start=(140,265),end=(140,318))
v("Controller API",150,282,130,20,TXT+"align=left;")
e(hri,hr,DSH,start=(850,735),end=(850,778))
v("HR/Payroll API",860,746,120,20,TXT+"align=left;")
czt=v("",988,439,20,2,"rounded=0;fillColor=none;strokeColor=none;")
e(bp,czt,DSH,start=(710,35),end=(998,439),pts=[(998,35)])
v("portal access to<br>central APIs",826,388,150,40,TXT+"align=right;")
epz=v("",130,759,20,2,"rounded=0;fillColor=none;strokeColor=none;")
e(ep,epz,DSH,start=(140,782),end=(140,761))

# broker texts on top of the arrows
v('<font style="font-size:14px">«message broker»</font>&nbsp;<b>Message broker</b>',292,303,366,24,f"text;align=center;fontSize=16;labelBackgroundColor=#f2f2f2;")
v("at-least-once; duplicates ignored by event ID",292,396,366,24,f"text;fontSize={TAG};fontColor=#555555;align=center;labelBackgroundColor=#f2f2f2;")

xml=('<mxfile host="drawio"><diagram name="Figure 4.15 C&amp;C View" id="Figure415CC"><mxGraphModel grid="1" gridSize="10" page="0" adaptiveColors="none"><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
     +''.join(c for c in cells if c)+'</root></mxGraphModel></diagram></mxfile>')
open(sys.argv[1],'w').write(xml)
