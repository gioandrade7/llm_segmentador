"""
diagnostico_erros.py — Classifica os erros de um ciclo contra o gabarito.

Cada nó sem bloco exato cai numa origem: fecho, último dispositivo (que absorve o
fecho), abertura, unidade engolida, bloco que engole, unidade sem bloco ou fim
errado. Cada bloco cujo início não é nó do gabarito conta como corte espúrio. As
origens somam exatamente os nós perdidos, o que `main` confere.

Uso:
    python diagnostico_erros.py resultados_amostra5/tcu           # último ciclo de cada norma da amostra
"""
import contextlib, glob, io, json, os, re, sys
S=os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0,S); os.chdir(S)
import avaliar as A
from gabarito import carregar_gabarito, _pagina
from anchor import _normalizar_texto_cached

_G={}
def gab(nome):
    if nome not in _G:
        with contextlib.redirect_stdout(io.StringIO()):
            _G[nome]=carregar_gabarito(f'{S}/gabarito/{nome}.md')
    return _G[nome]

def marcador(n):
    a=(n.get('ancora') or n.get('texto') or '').strip()
    a=re.sub(r'<[^>]*>|[#*_]','',a).strip()
    m=re.match(r"(?i)(CAP[IÍ]TULO\s+\w+|Se[çc][ãa]o\s+\w+|T[IÍ]TULO\s+\w+)",a)
    if m: return m.group(1)
    if n.get('tipo')=='epígrafe': return 'epígrafe'
    m=re.match(r"(Art\.?\s*\d+[º°o]?\.?(?:-[A-Z])?|§\s*\d+[º°]?|Par[áa]grafo\s+[úÚu]nico[.:]?|[IVXLC]+(?:\s[IVXLC]+)*\s*[-–—]|[a-zA-Z]\)|\d+(?:\.\d+)*[.)]?|\([a-zA-Z0-9]+\)|SEC\.\s*\d+[A-Z]?(?:-\d+)?\.?)",a)
    return m.group(1) if m else a[:30]

def trecho(texto,a,b=None,n=55):
    t=re.sub(r'<!--.*?-->','',texto[a:a+400 if b is None else b]); t=re.sub(r'<[^>]*>','',t); t=re.sub(r'[#*_]','',t); t=re.sub(r'\s+',' ',t).strip()
    return t[:n]

def alnum(norm,a,b): return sum(1 for c in norm[a:b] if c.isalnum())

def diag(nome,blocos):
    texto,nos=gab(nome)
    norm,mapa=_normalizar_texto_cached(texto)
    def fimtxt(i):
        p=mapa[i]; s=re.sub(r'<!--.*?-->|<[^>]*>|[#*_]','',texto[max(0,p-90):p+1]); return re.sub(r'\s+',' ',s).strip()[-50:]
    spans,perd=A.spans_previstos(texto,blocos)
    ig=A._para_normalizado(texto,[n['inicio'] for n in nos])
    iv=A._intervalos_de_fim(texto,nos)
    ip=A._para_normalizado(texto,[s for s,_ in spans])
    com=[k for k,(_,f) in enumerate(spans) if f!=-1]
    fp=A._fim_normalizado(texto,[spans[k][1] for k in com])
    fim_de={k:f for k,f in zip(com,fp)}
    cas=A.casar_fins(fp,iv,0)
    exato={}
    for i,j in cas.items():
        if ip[com[i]]==ig[j]: exato[j]=com[i]
    por_ini={}
    for k,p in enumerate(ip): por_ini.setdefault(p,k)
    gset=set(ig)
    r=dict(acertos=len(exato),nos=len(nos),blocos=len(spans),perdidos=perd,fecho=[],ultimo=[],abertura=None,eng={},sem=[],fim=[],esp=[],outros=[])
    fecho_js=[j for j,n in enumerate(nos) if n['tipo']=='fecho']
    primeiro=ig[0]
    def dono(pos):
        for k,(a,_) in enumerate(spans):
            f=fim_de.get(k)
            if f is not None and ip[k]<pos<=f: return k
        return None
    for j,n in enumerate(nos):
        if j in exato: continue
        pos=ig[j]; k=por_ini.get(pos)
        if n['tipo']=='epígrafe' and j==0 and k is None and dono(pos) is None:
            fimab=(ig[1] if len(ig)>1 else len(norm)); 
            prim=min(ip) if ip else fimab
            r['abertura']=dict(total=alnum(norm,pos,fimab),fora=alnum(norm,pos,min(prim,fimab)),trecho=trecho(texto,n['inicio'],n=70),completa=prim>=fimab)
            continue
        if n['tipo']=='epígrafe' and j==0 and k is not None:
            # abertura com bloco, mas fim fora do nó
            pass
        if k is None:
            d=dono(pos)
            if n['tipo']=='fecho':
                r['fecho'].append(dict(j=j,dentro=d,trecho=trecho(texto,n['inicio'])))
            elif d is not None:
                r['eng'].setdefault(d,[]).append(j)
            else:
                r['sem'].append(j)
            continue
        f=fim_de.get(k)
        if f is None:
            r['fim'].append(dict(j=j,tipo='sem_ancora')); continue
        a,b=iv[j]
        if f>b:
            # bloco passa do nó: engole o(s) seguinte(s)
            seg=[x for x in range(j+1,len(nos)) if ig[x]<=f]
            so_fecho=seg and all(nos[x]['tipo']=='fecho' for x in seg)
            r['ultimo' if so_fecho else 'engolidor' if False else 'ultimo' if so_fecho else 'englob'] = r.get('ultimo' if so_fecho else 'englob',[])+[j]
        elif f<a:
            r['fim'].append(dict(j=j,tipo='curto',bloco=fimtxt(f),no=fimtxt(a)))
        else:
            r['outros'].append(j)
    r.setdefault('englob',[])
    for k,p in enumerate(ip):
        if p not in gset:
            # nó dentro do qual caiu
            import bisect
            q=bisect.bisect_right(sorted(ig),p)-1
            jj=sorted(range(len(ig)),key=lambda x:ig[x])[q] if q>=0 else None
            r['esp'].append(dict(k=k,trecho=trecho(texto,spans[k][0]),pag=_pagina(texto,spans[k][0]),no=jj))
    r['_texto']=texto; r['_nos']=nos; r['_spans']=spans; r['_ig']=ig
    return r

def contagem(r):
    e=sum(len(v) for v in r['eng'].values())
    return dict(fecho=len(r['fecho']),ultimo=len(r['ultimo']),abertura=int(r['abertura'] is not None),sem=len(r['sem']),eng=e,engb=len(r['englob']),fim=len(r['fim']),esp=len(r['esp']),outros=len(r['outros']))

if __name__=='__main__':
    import collections
    man=[n['key'] for n in json.load(open('gabarito/tcu_amostra.json'))['normas']]
    base=sys.argv[1]
    tot=collections.Counter(); bad=[]
    for k in sorted(man):
        arq=sorted(glob.glob(f'{base}/{k}/{k}_ciclo*.json'),key=lambda p:int(re.search(r'ciclo(\d+)',p).group(1)))
        r=diag(k,json.load(open(arq[-1])))
        c=contagem(r); tot.update(c); tot['nos']+=r['nos']; tot['ac']+=r['acertos']
        perd=r['nos']-r['acertos']; dec=c['fecho']+c['ultimo']+c['abertura']+c['sem']+c['eng']+c['engb']+c['fim']+c['outros']
        if perd!=dec: bad.append((k,perd,dec,c))
    print(tot); print('nao fecha',bad)
