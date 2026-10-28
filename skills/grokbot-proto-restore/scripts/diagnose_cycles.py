#!/usr/bin/env python3
"""Diagnose cross-package reference cycles in the reconstructed proto graph.

Reuses the parsing half of extract_proto.py (everything before section 7),
then answers:
  1. how many cross-package edges are there, and between which packages
  2. are there *type-level* SCCs that span more than one package (hard blocker)
  3. which edges are the "back edges" that force folding
  4. what a minimal feed-back set would look like

Usage: python3 diagnose_cycles.py [path/to/proto.cjs]
"""
import collections
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = sys.argv[1] if len(sys.argv) > 1 else \
    "/tmp/grokbot_analysis/out/dist/electron-main/proto.cjs"

# --- reuse extract_proto.py's parsing (sections 1..6) -------------------------
src_mod = open(os.path.join(HERE, "extract_proto.py"), encoding="utf-8").read()
ns = {"__name__": "extract_proto_head",
      "__file__": os.path.join(HERE, "extract_proto.py"),
      "sys": sys}
exec(compile(src_mod, "extract_proto.py", "exec"), ns)
messages, enums, services, explicit = ns["main"](parse_only=True)

MAIN = "aiserver.v1"


def pkg_of_full(full):
    segs = full.split(".")
    for i, sg in enumerate(segs):
        if sg[:1].isupper():
            return ".".join(segs[:i])
    return ".".join(segs[:-1])


PKG = {}
for v, ms in messages.items():
    PKG[v] = pkg_of_full(ms["full"])
for v, e in enums.items():
    PKG[v] = pkg_of_full(e["full"])
FULL = {**{v: ms["full"] for v, ms in messages.items()},
        **{v: e["full"] for v, e in enums.items()}}


def refs_of(ms):
    out = []
    for f in ms["fields"]:
        if f["kind"] == "ref":
            out.append(f["var"])
        elif f["kind"] == "map" and f["V"]["kind"] == "ref":
            out.append(f["V"]["var"])
    return out


def svc_refs():
    out = []
    for svc in services:
        for mt in svc["methods"]:
            out.append((svc["name"], mt["in"]))
            out.append((svc["name"], mt["out"]))
    return out


EDGES = {v: [r for r in refs_of(ms) if r in PKG] for v, ms in messages.items()}

print("=" * 72)
print(f"types: {len(messages)} messages, {len(enums)} enums, {len(services)} services")
pk = collections.Counter(PKG.values())
print(f"packages: {dict(pk)}")

# --- 1. cross-package edges --------------------------------------------------
cross = collections.Counter()
cross_sample = collections.defaultdict(list)
for v, rs in EDGES.items():
    for r in rs:
        a, b = PKG[v], PKG[r]
        if a != b:
            cross[(a, b)] += 1
            if len(cross_sample[(a, b)]) < 3:
                cross_sample[(a, b)].append(f"{FULL[v]} -> {FULL[r]}")
print("\n" + "=" * 72)
print("cross-package reference edges (src pkg -> dst pkg):")
for (a, b), n in cross.most_common():
    print(f"  {a:>12} -> {b:<12} {n:5d}   e.g. {cross_sample[(a, b)][0]}")

# services also create package->package deps
svc_pk = collections.Counter()
for svc_name, tv in svc_refs():
    if tv in PKG:
        svc_pk[(svc_name.rpartition('.')[0], PKG[tv])] += 1
if svc_pk:
    print("\n  (service method signatures add:)")
    for (a, b), n in svc_pk.most_common():
        if a != b:
            print(f"  {a:>12} -> {b:<12} {n:5d}")

# --- 2. type-level SCC (Tarjan, iterative) -----------------------------------
index, low, onstk, stk, comp = {}, {}, set(), [], []
counter = [0]


def strongconnect(root):
    work = [(root, iter(EDGES.get(root, ())))]
    index[root] = low[root] = counter[0]; counter[0] += 1
    stk.append(root); onstk.add(root)
    while work:
        v, it = work[-1]
        for w in it:
            if w not in index:
                index[w] = low[w] = counter[0]; counter[0] += 1
                stk.append(w); onstk.add(w)
                work.append((w, iter(EDGES.get(w, ()))))
                break
            elif w in onstk:
                low[v] = min(low[v], index[w])
        else:
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
            if low[v] == index[v]:
                c = []
                while True:
                    w = stk.pop(); onstk.discard(w); c.append(w)
                    if w == v:
                        break
                comp.append(c)


for v in list(EDGES):
    if v not in index:
        strongconnect(v)

multi = [c for c in comp if len(c) > 1]
cross_pkg_scc = [c for c in multi if len({PKG[x] for x in c}) > 1]
print("\n" + "=" * 72)
print(f"type-level SCCs with >1 member : {len(multi)}")
print(f"  ... spanning >1 package      : {len(cross_pkg_scc)}   <-- hard blocker")
for c in cross_pkg_scc:
    print(f"    {[FULL[x] for x in c]}")
# self loops
selfl = [v for v, rs in EDGES.items() if v in rs]
print(f"  self-recursive messages      : {len(selfl)} (fine, same file)")
if multi:
    big = sorted(multi, key=len, reverse=True)[:5]
    print("\n  largest SCCs (any package):")
    for c in big:
        ps = sorted({PKG[x] for x in c})
        print(f"    size {len(c):4d}  pkgs={ps}  e.g. {FULL[c[0]]}")

# --- 3. package-level graph + back edges -------------------------------------
pg = collections.defaultdict(set)
for (a, b) in cross:
    pg[a].add(b)
for (a, b) in svc_pk:
    if a != b:
        pg[a].add(b)

print("\n" + "=" * 72)
print("package dependency graph:")
for a in sorted(pg):
    print(f"  {a} -> {sorted(pg[a])}")

# package SCC
pidx, plow, pon, pstk, pcomp = {}, {}, set(), [], []
pc = [0]


def pscc(root):
    work = [(root, iter(sorted(pg.get(root, ()))))]
    pidx[root] = plow[root] = pc[0]; pc[0] += 1
    pstk.append(root); pon.add(root)
    while work:
        v, it = work[-1]
        for w in it:
            if w not in pidx:
                pidx[w] = plow[w] = pc[0]; pc[0] += 1
                pstk.append(w); pon.add(w)
                work.append((w, iter(sorted(pg.get(w, ())))))
                break
            elif w in pon:
                plow[v] = min(plow[v], pidx[w])
        else:
            work.pop()
            if work:
                plow[work[-1][0]] = min(plow[work[-1][0]], plow[v])
            if plow[v] == pidx[v]:
                c = []
                while True:
                    w = pstk.pop(); pon.discard(w); c.append(w)
                    if w == v:
                        break
                pcomp.append(c)


for a in list(pg):
    if a not in pidx:
        pscc(a)

pc_multi = [c for c in pcomp if len(c) > 1]
print("\npackage-level cycles:", [sorted(c) for c in pc_multi])

# --- 4. what the naive fold actually moves -----------------------------------
fold = dict(PKG)
moved_set = []
while True:
    ch = False
    for v, rs in EDGES.items():
        if fold[v] == MAIN:
            continue
        for r in rs:
            if fold.get(r) == MAIN:
                fold[v] = MAIN; moved_set.append(v); ch = True; break
    if not changed if False else not ch:
        break
print("\n" + "=" * 72)
print(f"naive fold moves {len(moved_set)} types into {MAIN}:")
byp = collections.Counter(PKG[v] for v in moved_set)
print(f"  from packages: {dict(byp)}")
for v in moved_set[:20]:
    print(f"    {FULL[v]}")

# --- 5. minimal alternative: only break real cycles --------------------------
# types reachable (transitively) from MAIN and referencing back -> candidates
print("\n" + "=" * 72)
print("back-edge types (in a package-SCC, or on a cycle between packages):")
if pc_multi:
    incyc = set()
    for c in pc_multi:
        incyc |= set(c)
    bes = [(v, r) for v, rs in EDGES.items() for r in rs
           if PKG[v] in incyc and PKG[r] in incyc and PKG[v] != PKG[r]]
    print(f"  {len(bes)} such edges between {sorted(incyc)}")
    bysrc = collections.Counter(PKG[v] for v, r in bes)
    bydst = collections.Counter(PKG[r] for v, r in bes)
    print(f"  by src pkg: {dict(bysrc)}")
    print(f"  by dst pkg: {dict(bydst)}")
    for v, r in bes[:12]:
        print(f"    {FULL[v]} -> {FULL[r]}")

# --- 6. segmentation prototype -----------------------------------------------
# proto allows a package to span several .proto files; only *file-level* import
# cycles are rejected.  Types inside one file may reference each other freely.
# So: take a topological order of the type graph, greedily keep extending the
# current segment while nodes of the same package are available; every package
# switch starts a new segment.  Cross-file edges then only point forward.
print("\n" + "=" * 72)
print("segmentation (greedy topo order, same package kept together)")

comp_id = {}
for i, c in enumerate(comp):
    for x in c:
        comp_id[x] = i
CPKG = {}
for i, c in enumerate(comp):
    ps = {PKG[x] for x in c}
    assert len(ps) == 1, ("cross-package SCC", ps)
    CPKG[i] = ps.pop()

cadj = collections.defaultdict(set)
indeg = collections.Counter()
for v, rs in EDGES.items():
    for r in rs:
        a, b = comp_id[v], comp_id[r]
        if a != b and b not in cadj[a]:
            cadj[a].add(b); indeg[b] += 1
for i in range(len(comp)):
    indeg.setdefault(i, 0)

import heapq
ready = [i for i in range(len(comp)) if indeg[i] == 0]
heapq.heapify(ready)
order, cur_pkg, segments = [], None, []
while ready:
    pick = None
    for i in ready:
        if CPKG[i] == cur_pkg:
            pick = i; break
    if pick is None:
        pick = ready[0]
    ready.remove(pick); heapq.heapify(ready)
    if CPKG[pick] != cur_pkg:
        cur_pkg = CPKG[pick]
        segments.append([])
    segments[-1].append(pick)
    order.append(pick)
    for w in sorted(cadj[pick]):
        indeg[w] -= 1
        if indeg[w] == 0:
            heapq.heappush(ready, w)

print(f"  nodes (after SCC merge): {len(comp)}")
print(f"  segments (= .proto files for types): {len(segments)}")
segsizes = collections.Counter()
for s in segments:
    segsizes[CPKG[s[0]]] += 1
print(f"  files per package: {dict(segsizes)}")
for s in segments:
    names = [x for i in s for x in comp[i]]
    print(f"    {CPKG[s[0]]:<12} {len(names):5d} types   e.g. {FULL[names[0]]}")

# sanity: every cross-segment edge must point forward
pos = {}
for si, s in enumerate(segments):
    for i in s:
        pos[i] = si
bad = 0
for v, rs in EDGES.items():
    for r in rs:
        a, b = comp_id[v], comp_id[r]
        if pos[a] > pos[b]:
            bad += 1
print(f"  backward cross-file edges: {bad}   (must be 0)")
