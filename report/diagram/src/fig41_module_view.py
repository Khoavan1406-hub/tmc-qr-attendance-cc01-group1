"""Figure 4.1 Module View - compact layout (02/11/2026). Canvas ~900 x 900, 16-17 px text."""
from xml.sax.saxutils import escape
import sys
cells=[]; edges=[]; n=[0]; G={}
FONT="html=1;whiteSpace=wrap;fontFamily=Helvetica;"
BLUE="#1f4e79"
def q(s): return escape(s,{'"':"&quot;"})
def nid(p='c'):
    n[0]+=1; return f"{p}{n[0]}"
def v(val,x,y,w,h,style):
    i=nid(); G[i]=(x,y,w,h)
    cells.append(f'<mxCell id="{i}" value="{q(val)}" style="{style}{FONT}" vertex="1" parent="1"><mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')
    return i
def e(src,tgt,style,start=None,end=None,pts=(),label="",lx=0,ly=0):
    i=nid('e'); st=style
    if start:
        x,y,w,h=G[src]; st+=f"exitX={(start[0]-x)/w:.4f};exitY={(start[1]-y)/h:.4f};exitDx=0;exitDy=0;"
    if end:
        x,y,w,h=G[tgt]; st+=f"entryX={(end[0]-x)/w:.4f};entryY={(end[1]-y)/h:.4f};entryDx=0;entryDy=0;"
    g=f'<mxGeometry x="{lx}" y="{ly}" relative="1" as="geometry">'
    if pts: g+='<Array as="points">'+''.join(f'<mxPoint x="{a}" y="{b}"/>' for a,b in pts)+'</Array>'
    g+='</mxGeometry>'
    edges.append(f'<mxCell id="{i}" value="{q(label)}" style="{st}html=1;fontFamily=Helvetica;fontSize=14;labelBackgroundColor=#ffffff;" edge="1" parent="1" source="{src}" target="{tgt}">{g}</mxCell>')

ZONE="rounded=0;fillColor=#f2f2f2;strokeColor=none;"
ZL="text;align=left;verticalAlign=top;fontSize=15;fontColor=#555555;fontStyle=1;"
def zone(x,y,w,h,label,dashed=False):
    st="rounded=0;fillColor=none;strokeColor=#888888;dashed=1;strokeWidth=1.5;" if dashed else ZONE
    v("",x,y,w,h,st); v(label,x+10,y+5,w-20,22,ZL)
def pkg(name,x,y,w,h):
    tw=min(w-10,len(name.replace("&amp;","&"))*9.9+22)
    return v(f"<b>{name}</b>",x,y,w,h,f"shape=folder;tabWidth={tw:.0f};tabHeight=28;tabPosition=left;fillColor=#ffffff;strokeColor={BLUE};strokeWidth=2;fontColor={BLUE};verticalAlign=top;align=left;spacingLeft=8;spacingTop=1;fontSize=17;")
def iface(name,x,w,y,h=34,port=False):
    lab=(f'<font style="font-size:13px">«port»</font><br>{name}') if port else name
    return v(lab,x,y,w,h if not port else 44,"rounded=1;arcSize=18;fillColor=#ffffff;strokeColor=#333333;strokeWidth=1.3;fontSize=16;")
USE="endArrow=open;endSize=10;dashed=1;strokeColor=#000000;strokeWidth=1.3;edgeStyle=orthogonalEdgeStyle;rounded=0;"
REAL="endArrow=block;endFill=0;endSize=13;dashed=1;strokeColor=#000000;strokeWidth=1.3;edgeStyle=orthogonalEdgeStyle;rounded=0;"

A,B,C=(40,260),(330,270),(630,260)   # column x, width
def col(c,dx=20): return c[0]+dx, c[1]-2*dx
def lab(x,y,text="«use»",w=44):
    v(text,x-w/2,y-10,w,20,"text;align=center;verticalAlign=middle;fontSize=14;spacing=0;")
def inv(x,y,w=2,h=2): return v("",x,y,w,h,"rounded=0;fillColor=none;strokeColor=none;")

# ---------------- Clients
cl=pkg("Clients",150,10,610,74)
v("Station Client · Guard Status Display · Employee Portal · Back-office Portal",162,44,586,30,"text;align=center;verticalAlign=middle;fontSize=15;")

# ---------------- Cross-cutting
zone(20,120,890,166,"CROSS-CUTTING MODULES")
iac=pkg("Identity &amp; Access Control",A[0],154,A[1],112); auth=iface("AuthorizationApi",*col(A),204)
aud=pkg("Audit",B[0],154,B[1],112);                        audi=iface("AuditApi",*col(B),204)
mon=pkg("Monitoring &amp; Operations",C[0],154,C[1],112);  hea=iface("HealthApi",*col(C),204)

# ---------------- Domain
dom=v("",20,322,890,582,"rounded=0;fillColor=none;strokeColor=#888888;dashed=1;strokeWidth=1.5;")
v("DOMAIN MODULES",30,327,300,22,ZL)
zone(30,354,870,176,"FACTORY APPLICATION MODULES")
gi=pkg("Gate Intervention",A[0],386,A[1],132); gia=iface("GateInterventionApi",*col(A),440)
gc=pkg("Gate Core",B[0],386,B[1],132);         itc=iface("ITurnstileControl",*col(B),422,port=True); gca=iface("GateCoreApi",*col(B),474)
ti=pkg("Turnstile Integration",C[0],386,C[1],132); tad=iface("TurnstileAdapter",*col(C),427)

ss=pkg("Site Sync",B[0],546,B[1],86)
v("No public API. One instance at each factory and one at the central site.",B[0]+12,574,B[1]-24,54,"text;align=center;verticalAlign=middle;fontSize=14;fontColor=#444444;")

zone(30,646,870,250,"CENTRAL APPLICATION MODULES")
acc=pkg("Access &amp; Configuration",A[0],676,A[1],100); acca=iface("AccessConfigApi",*col(A),726)
att=pkg("Attendance",B[0],676,B[1],100);               atta=iface("AttendanceApi",*col(B),726)
ph=pkg("Payroll Handover",C[0],676,C[1],120);          pha=iface("PayrollHandoverApi",*col(C),708); ips=iface("IPayrollSubmission",*col(C),746,port=True)
rep=pkg("Reporting",B[0],798,B[1],90);                 repa=iface("ReportApi",*col(B),842)
hri=pkg("HR/Payroll Integration",C[0],812,C[1],80);    hra=iface("HrPayrollAdapter",*col(C),850)

# ---------------- dependencies (labels as separate text so they never sit on arrow heads)
e(cl,inv(454,119),USE,start=(455,84),end=(455,119)); lab(500,101)
e(cl,inv(909,340,2,20),USE,start=(760,47),end=(910,350),pts=[(924,47),(924,350)]); lab(842,33)
e(iac,audi,USE,start=(300,221),end=(350,221)); lab(325,201)
for tgt,x in ((auth,170),(audi,465),(hea,760)):
    e(dom,tgt,USE,start=(x,322),end=(x,238)); lab(x+30,302)
e(gi,gca,USE,start=(300,491),end=(350,491)); lab(325,471)
e(tad,itc,REAL,start=(650,444),end=(580,444))
e(ss,gca,USE,start=(465,546),end=(465,508)); lab(510,528)
e(ss,acca,USE,start=(330,610),end=(280,734),pts=[(315,610),(315,734)]); lab(345,652)
e(ss,atta,USE,start=(530,632),end=(530,726)); lab(560,656)
e(att,acca,USE,start=(330,752),end=(280,752)); lab(305,786)
e(ph,atta,USE,start=(630,752),end=(580,752)); lab(605,786)
e(hra,ips,REAL,start=(862,850),end=(862,790))
e(rep,atta,USE,start=(440,798),end=(440,760)); lab(490,787)
e(rep,acca,USE,start=(330,843),end=(170,760),pts=[(170,843)]); lab(250,829)

xml=('<mxfile host="drawio"><diagram name="Figure 4.1 Module View" id="Figure41ModuleView"><mxGraphModel grid="1" gridSize="10" page="0" adaptiveColors="none"><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
     +''.join(c for c in cells if c)+''.join(edges)+'</root></mxGraphModel></diagram></mxfile>')
open(sys.argv[1],'w').write(xml)
