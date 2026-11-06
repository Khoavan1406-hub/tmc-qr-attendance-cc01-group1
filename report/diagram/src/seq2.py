"""Sequence-diagram generator for draw.io, report style v2 (white, thin black lines,
stereotype headers, activation bars, numbered steps, boxed notes, legend)."""
from xml.sax.saxutils import escape
FONT="html=1;whiteSpace=wrap;fontFamily=Helvetica;"
def q(s): return escape(s,{'"':"&quot;"})

class Seq:
    def __init__(s,lifelines,x0=20,spacing=230,head_w=190,head_h=50,fs=16,top=10,first=34,
                 row=14,self_h=26,frame_hdr=30):
        s.cells=[];s.edges=[];s.back=[];s.n=0;s.lasty=0;s.fs=fs;s.row=row;s.self_h=self_h;s.fh=frame_hdr
        s.X={};s.act={};s.bars=[];s.frames=[];s.pending_extra=0
        for i,(k,st,name) in enumerate(lifelines):
            cx=x0+head_w/2+i*spacing; s.X[k]=cx
            lab=f'<font style="font-size:{fs-2}px">«{st}»</font><br><b>{name}</b>'
            s.v(lab,cx-head_w/2,top,head_w,head_h,f"rounded=1;arcSize=6;fillColor=#ffffff;strokeColor=#000000;strokeWidth=1.3;fontSize={fs};")
        s.left=x0-4; s.right=x0+head_w+(len(lifelines)-1)*spacing+4
        s.top=top+head_h; s.y=s.top+first
    # ------------------------------------------------------------ primitives
    def id(s,p='c'):
        s.n+=1; return f"{p}{s.n}"
    def v(s,val,x,y,w,h,style,back=False):
        i=s.id(); c=f'<mxCell id="{i}" value="{q(val)}" style="{style}{FONT}" vertex="1" parent="1"><mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>'
        (s.back if back else s.cells).append(c); return i
    def line(s,p1,p2,style,pts=(),back=False):
        i=s.id('e'); g=f'<mxGeometry relative="1" as="geometry"><mxPoint x="{p1[0]}" y="{p1[1]}" as="sourcePoint"/><mxPoint x="{p2[0]}" y="{p2[1]}" as="targetPoint"/>'
        if pts: g+='<Array as="points">'+''.join(f'<mxPoint x="{a}" y="{b}"/>' for a,b in pts)+'</Array>'
        g+='</mxGeometry>'
        c=f'<mxCell id="{i}" value="" style="{style}html=1;" edge="1" parent="1">{g}</mxCell>'
        (s.back if back else s.edges).append(c)
    ARROW={'sync':"endArrow=block;endFill=1;endSize=8;",
           'async':"endArrow=open;endFill=0;endSize=10;",
           'reply':"endArrow=open;endFill=0;endSize=10;dashed=1;dashPattern=6 4;"}
    def edge_x(s,k,toward):
        x=s.X[k]
        if k in s.act: x+= 6 if toward>x else -6
        return x
    def circle(s,num,cx,cy):
        s.v(f"<b>{num}</b>",cx-10,cy-10,20,20,f"ellipse;aspect=fixed;fillColor=#ffffff;strokeColor=#000000;strokeWidth=1.1;fontSize={s.fs-4};spacing=0;")
    # ------------------------------------------------------------ activations
    def on(s,k,dy=None): s.act[k]=s.y+(dy if dy is not None else s.fs*1.22-4)
    def off(s,k,pad=6):
        y1=s.act.pop(k); s.bars.append((k,y1,s.lasty+pad))
    # ------------------------------------------------------------ elements
    def _note(s,note,y):
        if not note: return 0
        text,x1,x2,lines=note; h=lines*s.fs*1.22+10
        s.v(text,x1,y,x2-x1,h,f"rounded=0;fillColor=#ffffff;strokeColor=#000000;strokeWidth=0.9;align=left;verticalAlign=middle;spacingLeft=6;spacingRight=4;fontSize={s.fs-1};")
        return h
    def msg(s,a,b,label,kind='sync',step=None,note=None,lines=1,activate=False,step_x=None,note_extra=True):
        lh=s.fs*1.22*lines
        s.y+=lh
        xa=s.edge_x(a,s.X[b]); xb=s.X[b]
        if activate: s.act[b]=s.y-4
        xb=s.edge_x(b,s.X[a])
        lo,hi=min(xa,xb),max(xa,xb)
        w=max(hi-lo,10)+300; cx=(lo+hi)/2
        x1=max(s.left,cx-w/2); x2=min(s.right,cx+w/2)
        s.v(label,x1,s.y-lh-3,x2-x1,lh,f"text;align=center;verticalAlign=bottom;fontSize={s.fs};spacing=0;labelBackgroundColor=#ffffff;")
        s.line((xa,s.y),(xb,s.y),s.ARROW[kind]+"strokeColor=#000000;strokeWidth=1.2;")
        if step: s.circle(step,step_x if step_x is not None else min(s.X[a],s.X[b])-34,s.y-2)
        s.lasty=s.y
        nh=s._note(note,s.y-lh-4)
        s.y+=max(s.row, (nh-lh+2) if note_extra else 0)
    def self_(s,a,label,step=None,note=None,w=330,side=None):
        x=s.edge_x(a,s.X[a]+1); h=s.self_h
        s.y+=8
        s.line((x,s.y),(x,s.y+h),s.ARROW['sync']+"strokeColor=#000000;strokeWidth=1.2;edgeStyle=none;",pts=[(x+28,s.y),(x+28,s.y+h)])
        s.v(label,x+36,s.y,w,h,f"text;align=left;verticalAlign=middle;fontSize={s.fs};labelBackgroundColor=#ffffff;")
        if step: s.circle(step,s.X[a]-34,s.y+h/2)
        s.lasty=s.y+h
        if side:
            sh=s._note(side,s.y-6); s.y+=max(h,sh-6)+s.row
        else:
            nh=s._note(note,s.y+h+6) if note else 0
            s.y+=h+s.row+(nh+8 if note else 0)
    def notebox(s,text,x1,x2,lines):
        h=s._note((text,x1,x2,lines),s.y); s.y+=h+s.row
    def frame(s,kind,guard,x1=None,x2=None):
        s.y+=6
        s.frames.append(dict(kind=kind,guard=guard,x1=x1 if x1 is not None else s.left-8,x2=x2 if x2 is not None else s.right+8,y=s.y,ops=[]))
        s.y+=s.fh
    def else_(s,guard):
        s.y+=4; s.frames[-1]['ops'].append((s.y,guard)); s.y+=(s.fh if guard else s.fh-12)
    def end(s):
        s.y+=4; f=s.frames.pop(); x1,x2,y1,y2=f['x1'],f['x2'],f['y'],s.y
        s.v("",x1,y1,x2-x1,y2-y1,"rounded=0;fillColor=none;strokeColor=#000000;strokeWidth=1.1;")
        tw=len(f['kind'])*9+16
        s.v(f"<b>{f['kind']}</b>",x1,y1,tw,22,f"rounded=0;fillColor=#ffffff;strokeColor=#000000;strokeWidth=1.1;fontSize={s.fs-1};spacing=0;")
        if f['guard']: s.v(f"[{f['guard']}]",x1+tw+10,y1+1,x2-x1-tw-16,22,f"text;align=left;verticalAlign=middle;fontSize={s.fs-1};fontStyle=2;labelBackgroundColor=#ffffff;")
        for yy,g in f['ops']:
            s.line((x1,yy),(x2,yy),"endArrow=none;dashed=1;dashPattern=8 4;strokeColor=#000000;strokeWidth=1.0;")
            if g: s.v(f"[{g}]",x1+tw+10,yy+3,x2-x1-tw-16,22,f"text;align=left;verticalAlign=middle;fontSize={s.fs-1};fontStyle=2;labelBackgroundColor=#ffffff;")
        s.y+=10
    def gap(s,d): s.y+=d
    def legend(s,extra=None):
        s.ll_end=s.y+4; s.y+=18; y=s.y; x=s.left+10
        def arrow(x,kind,text,w):
            s.line((x,y+10),(x+60,y+10),s.ARROW[kind]+"strokeColor=#000000;strokeWidth=1.2;")
            s.v(text,x+70,y,w,20,f"text;align=left;verticalAlign=middle;fontSize={s.fs-1};")
        arrow(x,'sync',"synchronous call",150)
        arrow(x+240,'async',"signal",80)
        arrow(x+400,'reply',"return",80)
        s.circle(1,x+575,y+10); s.v("step number used in the description",x+592,y,300,20,f"text;align=left;verticalAlign=middle;fontSize={s.fs-1};")
        s.y+=26
        if extra:
            s.v(extra,x,s.y,s.right-x,20,f"text;align=left;verticalAlign=middle;fontSize={s.fs-1};"); s.y+=24
    def xml(s,name,did):
        bottom=getattr(s,'ll_end',s.y-30)
        for k,x in s.X.items():
            s.line((x,s.top),(x,bottom),"endArrow=none;dashed=1;dashPattern=6 4;strokeColor=#777777;strokeWidth=1.0;",back=True)
        for k,y1,y2 in s.bars:
            s.v("",s.X[k]-6,y1,12,max(y2-y1,10),"rounded=0;fillColor=#ffffff;strokeColor=#000000;strokeWidth=1.1;",back=True)
        # bars must sit above lifelines but below arrows/labels: back list holds lifelines first then bars
        return (f'<mxfile host="drawio"><diagram name="{q(name)}" id="{did}"><mxGraphModel grid="1" gridSize="10" page="0" adaptiveColors="none"><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
                +''.join(s.back)+''.join(s.cells)+''.join(s.edges)+'</root></mxGraphModel></diagram></mxfile>')
