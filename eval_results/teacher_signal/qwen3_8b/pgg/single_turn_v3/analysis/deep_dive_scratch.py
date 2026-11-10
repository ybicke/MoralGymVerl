import json,re,glob,collections,math,sys
sys.path.insert(0,'/users/bickery/MoralGymVerl/scripts/analysis')
from pgg_label_valence import valence
ROOT='/users/bickery/MoralGymVerl/eval_results/teacher_signal/pgg_single_turn_qwen3-8b_v3/cells/public_goods__'
ARMS=['none','deontological','utilitarian','universalization']
def sc(m,k): return 5*(k+1) if m=='C' else 10+5*k
def st(p):
    C=re.search(r'\| others choosing (action\d)',p).group(1)
    own=re.search(r'Last round, you chose (action\d)',p).group(1); own='C' if own==C else 'D'
    k=re.search(r'Of the other 3 players, (all 3|\d) chose (action\d)',p); n=3 if k.group(1)=='all 3' else int(k.group(1)); k=n if k.group(2)==C else 3-n
    return own,k
def wilson(x,n,z=1.96):
    if n==0: return (0,0)
    p=x/n; d=1+z*z/n; c=(p+z*z/(2*n))/d; h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return (100*(c-h),100*(c+h))
def load(v):
    f=glob.glob(ROOT+v+'__decision_full*/behavioral.responses.jsonl')[0]; out=[]
    for l in open(f):
        r=json.loads(l); own,k=st(r['prompt']); raw=r['raw']
        a=re.findall(r'Action:\s*\*?\*?(action\d)',raw); act=('C' if a[-1]=='action3' else 'D') if a else '?'
        out.append(dict(own=own,k=k,act=act,raw=raw))
    return out
def blk(v):
    d=json.load(open(glob.glob(ROOT+v+'__decision_full*/behavioral.json')[0]))['opponents'][0]; return d
# ---- regex typology
T={
 'dominance':      r'dominat|always (?:gives?|yields?|results? in|better|higher|more)|in (?:every|all) (?:case|scenario|row)s?|regardless of (?:what|how many)',
 'group_total':    r'(?:total|combined)[^\n]{0,60}=\s*\**\d+',
 'universalize':   r'if everyone (?:chose|choose|chooses|did|acted)|everyone (?:in your situation|choosing|chose)|general adoption|all (?:4|four) (?:players )?(?:chose|choose)',
 'unilateral':     r'unilateral|alone (?:switch|choose)|only (?:you|one) (?:switch|choose|contribut)|if (?:you|i) (?:switch|change) (?:to|and) [^\n]{0,40}(?:others|they) (?:still|continue|keep|stick)',
 'risk':           r'\brisk\w*|uncertain|not guaranteed|gamble',
 'future':         r'future rounds?|next rounds?|encourage (?:others|them)|signal|influence (?:others|the group)|build trust|long[- ]term',
 'good_faith':     r'good faith',
 'fair':           r'\bfair(?:ness)?\b|equal(?:ly|ity)?\b',
 'sucker':         r'sucker|taken advantage of you|exploited you|at your expense',
 'moral_override': r'(?:despite|although|even though|but|however)[^\n]{0,80}moral|moral[^\n]{0,80}(?:outweigh|take[s]? precedence|more important|over(?:rides?)?)',
}
T={k:re.compile(p,re.I) for k,p in T.items()}
def util_total_class(r):
    own,k=r['own'],r['k']; raw=r['raw']
    res={}
    for mv in 'CD':
        mine=sc(mv,k); e=1 if mv=='C' else 0
        pc=sc('C',k-1+e) if k>0 else None; pd=sc('D',k+e) if k<3 else None
        true=mine+(k*pc if pc else 0)+((3-k)*pd if pd else 0)
        slip=mine+(pc or 0)+(pd or 0)
        res[mv]=(true,slip)
    vals=[]
    for line in raw.split('\n'):
        if re.search(r'total|combined',line,re.I):
            m=re.findall(r'=\s*\**(\d+)\**',line)
            if m: vals.append(int(m[-1]))
    s=set(vals)
    if not s: return 'no_total'
    trues={res['C'][0],res['D'][0]}; slips={res['C'][1],res['D'][1]}
    if s<=trues: return 'true'
    if s<=trues|slips and s&slips: return 'multiplicity_slip'
    return 'other_wrong'
def stated_pref(raw):
    # last explicit recommendation before the Action line
    body=raw.rsplit('Action:',1)[0]
    m=list(re.finditer(r'(?:should|best|better|optimal|correct|right)[^\n.]{0,40}?(?:choice|action|option|decision)?[^\n.]{0,30}?\b(action[34])\b|(?:choose|pick|go with|stick with|select)\s+\**(action[34])',body,re.I))
    if not m: return None
    g=m[-1]; return g.group(1) or g.group(2)
def own_payoff_misread(r):
    # claims action3 pays the AGENT more than action4
    return bool(re.search(r'action3[^\n.]{0,60}(?:gives?|yields?|earns?|pays?)[^\n.]{0,20}(?:you )?(?:more|higher)[^\n.]{0,30}(?:than|over) [^\n.]{0,10}action4',r['raw'],re.I))

out=[]
P=lambda *a: out.append(' '.join(str(x) for x in a))
data={v:load(v) for v in ARMS}
P('## A. Cell headline metrics (behavioral.json → pgg block + reward channels)\n')
P('| arm | k_slope C_A | k_slope D_A | group_eff | sucker (kprev) | freeride (kprev) | mean_r_game | mean_r_deon | mean_r_util |'); P('|---|---|---|---|---|---|---|---|---|')
for v in ARMS:
    b=blk(v); p=b['pgg']
    P(f"| {v} | {p['k_slope']['C']:+.3f} | {p['k_slope']['D']:+.3f} | {p['group_efficiency']:.3f} | {p['sucker_rate_kprev']:.3f} | {p['freeride_on_contributors_kprev']:.3f} | {b['mean_r_game']:.3f} | {b['mean_r_deon']:.3f} | {b['mean_r_util']:.3f} |")
P('\n## B. Per-state P(C) with Wilson 95% CI (n=50)\n')
P('| arm | own | k=0 | k=1 | k=2 | k=3 | pooled |'); P('|---|---|---|---|---|---|---|')
for v in ARMS:
    for own in 'CD':
        cells=[]; tx=tn=0
        for k in range(4):
            rs=[r for r in data[v] if r['own']==own and r['k']==k]; x=sum(r['act']=='C' for r in rs); n=len(rs); tx+=x; tn+=n
            lo,hi=wilson(x,n); cells.append(f'{100*x/n:.0f} [{lo:.0f}–{hi:.0f}]')
        lo,hi=wilson(tx,tn); P(f"| {v} | {own} | "+' | '.join(cells)+f" | {100*tx/tn:.0f} [{lo:.0f}–{hi:.0f}] |")
P('\n## C. Own-move effect and repeat rate\n')
P('| arm | P(C|C_A) − P(C|D_A) (pts) | repeat own move % | switch C→D % | switch D→C % |'); P('|---|---|---|---|---|')
for v in ARMS:
    pc={o:sum(r['act']=='C' for r in data[v] if r['own']==o)/200 for o in 'CD'}
    rep=sum(r['act']==r['own'] for r in data[v])/4
    cd=sum(r['own']=='C' and r['act']=='D' for r in data[v])/2; dc=sum(r['own']=='D' and r['act']=='C' for r in data[v])/2
    P(f"| {v} | {100*(pc['C']-pc['D']):+.0f} | {rep:.0f} | {cd:.0f} | {dc:.0f} |")
P('\n## D. Rationale typology (regex, % of traces; P(C) within tagged subgroup)\n')
P('| feature | '+' | '.join(f'{v} % (P(C))' for v in ARMS)+' |'); P('|---|'+'---|'*len(ARMS))
for name,rx in T.items():
    row=[]
    for v in ARMS:
        tag=[r for r in data[v] if rx.search(r['raw'])]; n=len(tag)
        pcv=f"{100*sum(r['act']=='C' for r in tag)/n:.0f}" if n else '—'
        row.append(f'{100*n/400:.0f} ({pcv})')
    P(f'| {name} | '+' | '.join(row)+' |')
P('\n## E. Deontological: label valence × k (validated regex), P(C) within group\n')
P('| own | k | inverted % (P(C)) | correct % (P(C)) | both % | silent % (P(C)) |'); P('|---|---|---|---|---|---|')
for own in 'CD':
    for k in range(4):
        rs=[r for r in data['deontological'] if r['own']==own and r['k']==k]
        g=collections.defaultdict(list)
        for r in rs: g[valence(r['raw'])].append(r['act']=='C')
        f=lambda key: f"{100*len(g[key])/50:.0f} ({100*sum(g[key])/len(g[key]):.0f})" if g[key] else '0 (—)'
        P(f"| {own} | {k} | {f('inverted')} | {f('correct')} | {100*len(g['both'])/50:.0f} | {f('silent')} |")
P('\n## F. Utilitarian: stated group-total arithmetic\n')
cls=collections.Counter(); pcs=collections.defaultdict(list); byk=collections.defaultdict(collections.Counter)
for r in data['utilitarian']:
    c=util_total_class(r); cls[c]+=1; pcs[c].append(r['act']=='C'); byk[r['k']][c]+=1
P('| class | n | % | P(C) |'); P('|---|---|---|---|')
for c in ['true','multiplicity_slip','other_wrong','no_total']:
    n=cls[c]; P(f"| {c} | {n} | {100*n/400:.0f} | {100*sum(pcs[c])/n:.0f} |" if n else f"| {c} | 0 | 0 | — |")
P('\nby k (n per class): '+'; '.join(f"k={k}: "+', '.join(f'{c}={byk[k][c]}' for c in ['true','multiplicity_slip','other_wrong','no_total']) for k in range(4)))
P('\n## G. Rationale–decision consistency and own-payoff misreads\n')
P('| arm | stated pref found % | pref ≠ Action % (of found) | claims action3 pays self more % |'); P('|---|---|---|---|')
for v in ARMS:
    f=[(stated_pref(r['raw']),r) for r in data[v]]; found=[(p,r) for p,r in f if p]
    mism=sum(('C' if p=='action3' else 'D')!=r['act'] for p,r in found)
    mis=sum(own_payoff_misread(r) for r in data[v])
    P(f"| {v} | {100*len(found)/400:.0f} | {100*mism/max(1,len(found)):.0f} | {100*mis/400:.1f} |")
P('\n## H. Trace length (words)\n')
P('| arm | median | p90 | max | ≥1500 words % |'); P('|---|---|---|---|---|')
for v in ARMS:
    L=sorted(len(r['raw'].split()) for r in data[v]); n=len(L)
    P(f"| {v} | {L[n//2]} | {L[int(.9*n)]} | {L[-1]} | {100*sum(x>=1500 for x in L)/n:.0f} |")
open('/tmp/claude-1843/-users-bickery/650c7035-8dde-495f-8a48-e8d191c7060f/scratchpad/deep_dive.md','w').write('\n'.join(out))
print('\n'.join(out))
