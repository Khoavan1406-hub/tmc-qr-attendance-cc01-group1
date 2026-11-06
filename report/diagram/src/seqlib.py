"""Tiny sequence-diagram generator for draw.io (white style used in the TMC report)."""
from xml.sax.saxutils import escape
BLUE="#1f4e79"
FONT="html=1;whiteSpace=wrap;fontFamily=Helvetica;"
def q(s): return escape(s,{'"':"&quot;"})

class Seq:
    def __init__(s,lifelines,x0=20,spacing=170,head_w=156,head_h=58,msg=18,title=18,sub=14,top=10,gm=20,gs=17,gf=40,first=24,after_self=12,after_frame=0,pad_end=0):
        s.cells=[];s.n=0;s.fs=msg;s.gm=gm;s.gs=gs;s.gf=gf;s.a_self=after_self;s.a_frame=after_frame;s.pad_end=pad_end
        s.X={};s.order=[k for k,_,_ in lifelines]
        s.x0=x0;s.sp=spacing;s.hw=head_w;s.hh=head_h
        for i,(k,t,sb) in enumerate(lifelines):
            cx=x0+head_w/2+i*spacing; s.X[k]=cx
            lab=f'<b>{t}</b>'+(f'<br><font style="font-size:{sub}px;color:#555555">{sb}</font>' if sb else '')
            s.v(lab,cx-head_w/2,top,head_w,head_h,f"rounded=0;fillColor=#ffffff;strokeColor={BLUE};strokeWidth=2;fontSize={title};")
        s.left=x0-6; s.right=x0+head_w+(len(lifelines)-1)*spacing+6
        s.y=top+head_h+first; s.top=top+head_h
        s.frames=[]; s.last=None
    def id(s,p='c'):
        s.n+=1; return f"{p}{s.n}"
    def v(s,val,x,y,w,h,style):
        i=s.id(); s.cells.append(f'<mxCell id="{i}" value="{q(val)}" style="{style}{FONT}" vertex="1" parent="1"><mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>'); return i
    def line(s,p1,p2,style,pts=()):
        i=s.id('e'); g=f'<mxGeometry relative="1" as="geometry"><mxPoint x="{p1[0]}" y="{p1[1]}" as="sourcePoint"/><mxPoint x="{p2[0]}" y="{p2[1]}" as="targetPoint"/>'
        if pts: g+='<Array as="points">'+''.join(f'<mxPoint x="{a}" y="{b}"/>' for a,b in pts)+'</Array>'
        g+='</mxGeometry>'
        s.cells.insert(0 if 'LIFELINE' in style else len(s.cells), f'<mxCell id="{i}" value="" style="{style.replace("LIFELINE;","")}html=1;" edge="1" parent="1">{g}</mxCell>')
    # ---------------------------------------------------------------- elements
    def phase(s,title):
        s.y+=6
        s.v(title,s.left-14,s.y,s.right-s.left+28,30,f"rounded=0;fillColor=#f2f2f2;strokeColor=none;align=left;spacingLeft=10;fontSize=17;fontStyle=1;fontColor=#555555;")
        s.y+=30+24
    ARROW={'sync':"endArrow=block;endFill=1;endSize=9;",
           'async':"endArrow=open;endFill=0;endSize=11;",
           'reply':"endArrow=open;endFill=0;endSize=11;dashed=1;"}
    def msg(s,a,b,label,kind='sync',lines=1,side=None):
        xa,xb=s.X[a],s.X[b]; lh=s.fs*1.25*lines; s.y+=(lines-1)*s.fs*1.25+(s.a_self if s.last=="self" else s.a_frame if s.last=="frame" else 0); s.last="msg"
        lo,hi=min(xa,xb),max(xa,xb); w=max(hi-lo,10)+420; cx=(lo+hi)/2
        x1=max(s.left,cx-w/2); x2=min(s.right,cx+w/2)
        s.v(label,x1,s.y-lh-2,x2-x1,lh,f"text;align=center;verticalAlign=bottom;fontSize={s.fs};labelBackgroundColor=#ffffff;spacing=0;")
        s.line((xa,s.y),(xb,s.y),s.ARROW[kind]+"strokeColor=#000000;strokeWidth=1.4;")
        if side:
            t,sx1,sx2,sl=side
            lh1=s.fs*1.25
            s.v(t,sx1,s.y-lh1-9,sx2-sx1,lh1*sl+4,f"text;align=left;verticalAlign=top;fontSize={s.fs-1};fontColor=#333333;labelBackgroundColor=#ffffff;fontStyle=2;spacingTop=0;")
            s.extra=max(0,(sl-1)*lh1-8)
        s.y+=s.fs*1.25+s.gm+getattr(s,'extra',0); s.extra=0
    def self_(s,a,label,lines=1,left=False,w=420):
        x=s.X[a]; h=max(26,s.fs*1.25*lines); s.y+=(s.a_frame if s.last=="frame" else 0); s.last="self"
        s.y-=8
        s.line((x,s.y),(x,s.y+h),s.ARROW['sync']+"strokeColor=#000000;strokeWidth=1.4;edgeStyle=none;",pts=[(x+32,s.y),(x+32,s.y+h)])
        if left: s.v(label,x-w-10,s.y-2,w,h+4,f"text;align=right;verticalAlign=middle;fontSize={s.fs};labelBackgroundColor=#ffffff;")
        else: s.v(label,x+42,s.y-2,w,h+4,f"text;align=left;verticalAlign=middle;fontSize={s.fs};labelBackgroundColor=#ffffff;")
        s.y+=h+s.gs
    def frame(s,kind,guard,a=None,b=None,pad=60,padr=None):
        s.y+=6+(s.pad_end if s.last=="self" else 0); x1=(s.X[a]-pad) if a else s.left; x2=(s.X[b]+(padr if padr is not None else pad)) if b else s.right
        s.frames.append(dict(kind=kind,x1=x1,x2=x2,y=s.y-16,guard=guard,ops=[]))
        s.y+=s.gf; s.last="frame"
    def else_(s,guard):
        s.y+=s.pad_end; f=s.frames[-1]; f['ops'].append((s.y-12,guard)); s.last="frame"; s.y+=(s.gf if guard else s.gf-12)
    def end(s):
        s.y+=s.pad_end; f=s.frames.pop(); y2=s.y-8; x1,x2,y1=f['x1'],f['x2'],f['y']
        s.v(f['kind'],x1,y1,x2-x1,y2-y1,f"shape=umlFrame;width={66 if f['kind']=='break' else 58};height=26;fillColor=none;strokeColor=#333333;strokeWidth=1.3;fontStyle=1;fontSize={min(16,s.fs)};verticalAlign=top;align=left;spacingLeft=10;spacingTop=1;swimlaneFillColor=none;")
        if f['guard']: s.v(f"[{f['guard']}]",x1+(72 if f['kind']=='break' else 64),y1+2,x2-x1-70,24,f"text;align=left;verticalAlign=middle;fontSize={s.fs-1};fontStyle=2;")
        for (yy,g) in f['ops']:
            s.line((x1,yy),(x2,yy),"endArrow=none;dashed=1;strokeColor=#333333;strokeWidth=1.2;")
            if g: s.v(f"[{g}]",x1+8,yy+2,x2-x1-16,24,f"text;align=left;verticalAlign=middle;fontSize={s.fs-1};fontStyle=2;")
        s.y+=14
    def note(s,text,a=None,b=None,h=None,pad=30):
        x1=(s.X[a]-pad) if a else s.left+10; x2=(s.X[b]+pad) if b else s.right-10
        h=h or int(s.fs*1.35)+14
        s.y+=8
        s.v(text,x1,s.y-12,x2-x1,h,f"shape=note;size=12;fillColor=#ffffff;strokeColor=#888888;align=left;spacingLeft=8;spacingRight=14;fontSize={s.fs-1};")
        s.y+=h+18
    def gap(s,d): s.y+=d
    def xml(s,name,did):
        bottom=s.y+6
        for k,x in s.X.items():
            s.line((x,s.top),(x,bottom),"LIFELINE;endArrow=none;dashed=1;strokeColor=#888888;strokeWidth=1.2;")
        return (f'<mxfile host="drawio"><diagram name="{q(name)}" id="{did}"><mxGraphModel grid="1" gridSize="10" page="0" adaptiveColors="none"><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
                +''.join(s.cells)+'</root></mxGraphModel></diagram></mxfile>')
