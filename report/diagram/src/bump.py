import re,sys
for f in sys.argv[1:]:
    s=open(f).read()
    def cell(m):
        c=m.group(0)
        if 'edge="1"' in c and 'value="«use»"' in c:
            c=re.sub(r'fontSize=\d+;?','',c); c=c.replace('style="','style="fontSize=14;',1)
        elif re.search(r'fontSize=11;',c) and 'vertex="1"' in c:
            c=c.replace('fontSize=11;','fontSize=14;')
        return c
    s=re.sub(r'<mxCell [^>]*>',cell,s)
    s=s.replace('font-size:12px','font-size:14px').replace('font-size:11px','font-size:13px')
    open(f,'w').write(s)
