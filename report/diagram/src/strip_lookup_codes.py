import re,sys,glob,html
def fix(s):
    # module view: "M13 Clients" -> "Clients", recompute folder tab widths
    def tab(m):
        cell=m.group(0)
        v=re.search(r'value="([^"]*)"',cell)
        val=v.group(1)
        new=re.sub(r'(&lt;b&gt;)M\d{1,2} (?!\|)',r'\1',val)
        if new!=val:
            o=len(html.unescape(html.unescape(re.sub(r'&lt;.*?&gt;','',val)))); n=len(html.unescape(html.unescape(re.sub(r'&lt;.*?&gt;','',new))))
            cell=cell.replace(val,new)
            cell=re.sub(r'tabWidth=([\d.]+)',lambda t:f"tabWidth={float(t.group(1))*n/o:.1f}",cell)
        return cell
    s=re.sub(r'<mxCell [^>]*shape=folder[^>]*>',tab,s)
    s=re.sub(r'(&lt;b&gt;)M\d{1,2} \| ',r'\1',s)
    for k in ["domain module","cross-cutting module","integration module","client module"]:
        s=s.replace(f'&gt;{k} ·',f'&gt;«{k}» ·')
    s=re.sub(r'&lt;br&gt;\(M\d{1,2}\)','',s)
    s=re.sub(r' \(M\d{1,2}\)','',s)
    s=re.sub(r' · I-\d\d(, I-\d\d)*','',s)
    s=re.sub(r' \(I-\d\d(, I-\d\d)*\)','',s)
    for a,b in [("ENTRY","Entry"),("BUSINESS","Business"),("PERSISTENCE &amp; PORTS","Persistence &amp; Ports"),("PERSISTENCE","Persistence"),("ADAPTER","Adapter")]:
        s=s.replace(f'value="{a}"',f'value="«layer» {b}"')
    return s
for f in sys.argv[1:]:
    t=open(f,encoding='utf8').read(); n=fix(t)
    open(f,'w',encoding='utf8').write(n)
    left=re.findall(r'M\d{1,2}\b[^a-z]|I-\d\d',re.sub(r'<mxGeometry.*?</mxGeometry>|style="[^"]*"|id="[^"]*"','',n))
    print(f, 'changed' if n!=t else 'same', 'left:',left[:5])
