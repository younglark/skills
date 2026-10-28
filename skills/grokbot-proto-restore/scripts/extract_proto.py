#!/usr/bin/env python3
"""
Reconstruct real .proto files from the minified @bufbuild/protobuf bundle
(dist/electron-main/proto.cjs) shipped inside Grok Bot.

Why this works
--------------
The bundle stores every generated type in a *compact descriptor*:

    C1 = class e extends n {
        ...
        static $()  { return ["SendGrokBotUserMessageRequest|1 agent_id 9|2 text 9|13 source #0", Kg, Hg, Wg] }
    }
    x  = class extends f { static $p() { return ot } }        // ot = "aiserver.v1."
    Kg = a(s, S, "GrokBotClientSurface", [[0,"UNSPECIFIED"],[1,"DESKTOP"]], 1)

  typeName = $p() + $() [0] up to the first "|"
  fields   = $() [0] split on "|": "<no> <name> <type>[?|*][ oneof]"
             type  = protobuf ScalarType number, or "#N" -> $()[N+1] (message if fn, enum otherwise)
             ","   -> map field: "<keyScalar>,<valueType>"
  enum value names = UPPER_SNAKE(localName) + "_" + short   (when flags === 1)

Usage:  python3 extract_proto.py [proto.cjs] [outDir]
"""

import re, os, sys, json, collections

SCALAR = {
    1: "double", 2: "float", 3: "int64", 4: "uint64", 5: "int32", 6: "fixed64",
    7: "fixed32", 8: "bool", 9: "string", 10: "group", 11: "message", 12: "bytes",
    13: "uint32", 14: "enum", 15: "sfixed32", 16: "sfixed64", 17: "sint32", 18: "sint64",
}

def upper_snake(name: str) -> str:
    return (name[0] + name[1:]).replace("_", "")[:0] + \
           re.sub(r"[A-Z]", lambda m: "_" + m.group(0), name).upper()

def TW(d: str) -> str:
    # function TW(e){return(e.charAt(0)+e.slice(1).replace(/[A-Z]/g,t=>"_"+t)).toUpperCase()+"_"}
    return (d[0] + d[1:].replace("__", "")).upper() if False else \
           (d[0] + re.sub(r"[A-Z]", lambda m: "_" + m.group(0), d[1:])).upper() + "_"


def split_top_level(text: str):
    """Split a JS array literal body on top-level commas."""
    out, buf, depth, in_str, esc = [], [], 0, False, False
    for ch in text:
        if in_str:
            buf.append(ch)
            if esc: esc = False
            elif ch == "\\": esc = True
            elif ch == '"': in_str = False
            continue
        if ch == '"': in_str = True; buf.append(ch); continue
        if ch in "([{": depth += 1
        elif ch in ")]}": depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(buf).strip()); buf = []
        else:
            buf.append(ch)
    if "".join(buf).strip():
        out.append("".join(buf).strip())
    return out


def balanced(text: str, open_i: int):
    """Return index just past the brace/bracket group starting at text[open_i]."""
    pairs = {"(": ")", "[": "]", "{": "}"}
    close = pairs[text[open_i]]
    depth, i, in_str, esc = 0, open_i, False, False
    while i < len(text):
        ch = text[i]
        if in_str:
            if esc: esc = False
            elif ch == "\\": esc = True
            elif ch == '"': in_str = False
        else:
            if ch == '"': in_str = True
            elif ch in pairs: depth += 1
            elif ch == close:
                depth -= 1
                if depth == 0: return i + 1
        i += 1
    return len(text)


def parse_compact(compact: str, refs, self_var=None, self_inner=None):
    """Parse 'Name|no name type[?*][ oneof]|...' -> list of field dicts."""
    parts = compact.split("|")
    fields = []
    for seg in parts[1:]:
        if not seg.strip():
            continue
        g = seg.split(" ")
        if len(g) < 3:
            continue
        no, name, typ = int(g[0]), g[1], g[2]
        mod = g[3] if len(g) > 3 else None
        if typ and typ[-1] in "?*":
            mod = typ[-1]
            typ = typ[:-1]
        if "," in typ:                       # map field
            k, v = typ.split(",", 1)
            fields.append({"no": no, "name": name, "kind": "map",
                           "K": int(k), "V": resolve(v, refs, self_var, self_inner)})
            continue
        f = {"no": no, "name": name}
        t = resolve(typ, refs, self_var, self_inner)
        f.update(t)
        if mod == "?": f["opt"] = True
        elif mod == "*": f["repeated"] = True
        elif mod: f["oneof"] = mod
        fields.append(f)
    return fields


def resolve(typ, refs, self_var=None, self_inner=None):
    if not typ.startswith("#"):
        return {"kind": "scalar", "T": SCALAR.get(int(typ), "string")}
    idx = int(typ[1:])
    if idx < len(refs):
        rv = refs[idx]
        # 自引用：minified 类用内部名（如 class e extends X 里的 e）指向自己
        if self_inner and rv == self_inner:
            rv = self_var
        return {"kind": "ref", "var": rv}
    return {"kind": "scalar", "T": "string"}


def main(parse_only=False):
    src = sys.argv[1] if len(sys.argv) > 1 else \
        "/tmp/grokbot_analysis/out/dist/electron-main/proto.cjs"
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.dirname(os.path.abspath(__file__))
    s = open(src, encoding="utf-8", errors="replace").read()

    # ---- 1. scope strings  (ot = "aiserver.v1.")
    scope = {}
    for m in re.finditer(r'\b(\w+)="([a-z][A-Za-z0-9]*(?:\.[A-Za-z0-9]+)*\.)"', s):
        scope[m.group(1)] = m.group(2)

    # ---- 2. per-package base classes:  x = class extends f { ... static $p(){return ot}
    base_scope = {}
    for m in re.finditer(r'([A-Za-z_$][A-Za-z0-9_$]*)=class(?: \w+)? extends f\{(.{0,400}?)static \$p\(\)\{return ([A-Za-z_$][A-Za-z0-9_$]*)\}', s, re.S):
        base_scope[m.group(1)] = scope.get(m.group(3))

    # ---- 3. explicit typeName assignments (google.protobuf.* etc.)
    explicit = dict(re.findall(r'([A-Za-z_$][A-Za-z0-9_$]*)\.typeName="([^"]+)"', s))

    # ---- 4. enums:  VAR = a(s, SCOPE, "Name", [[0,"X"],...], 1)
    enums = {}     # var -> {full, values:[(name,no)]}
    EM = r'([A-Za-z_$][A-Za-z0-9_$]*)'
    # 注意：作用域参数可能是 "$$"/"$x"，必须允许 $ 开头
    for m in re.finditer(EM + r'=a\(([A-Za-z_$][\w$]*),([A-Za-z_$][\w$]*),"([^"]+)",(\[\[.*?\]\])(?:,([^)]*))?\)', s, re.S):
        var, _, sc, name, vals, flags = m.groups()
        prefix = scope.get(sc, "aiserver.v1.")
        local = name.split(".")[-1]
        pfx = TW(local) if (flags or "").strip() == "1" else ""
        values = []
        for vm in re.finditer(r'\[(-?\d+),"([^"]+)"\]', vals):
            values.append((pfx + vm.group(2), int(vm.group(1))))
        enums[var] = {"full": prefix + name, "values": values}

    # ---- 5. messages:  VAR = class e extends BASE { ... static $(){return[...]} }
    class_pos = [(m.start(), m.group(1), m.group(2), m.group(3))
                 for m in re.finditer(r'([A-Za-z_$][A-Za-z0-9_$]*)=class(?: ([A-Za-z_$][\w$]*))? extends ([A-Za-z_$][A-Za-z0-9_$]*)\{', s)]
    class_pos.sort()
    import bisect
    starts = [c[0] for c in class_pos]

    unknown_bases = collections.Counter()
    messages = {}    # var -> {full, fields, pkg}
    for m in re.finditer(r'static \$\(\)\{return\[', s):
        arr_i = m.end() - 1
        end_i = balanced(s, arr_i)
        body = s[arr_i + 1:end_i - 1]
        els = split_top_level(body)
        if not els:
            continue
        compact = els[0].strip().strip('"')
        refs = [e.strip().strip('"') for e in els[1:]]
        name = compact.split("|")[0]
        if not name:
            continue
        k = bisect.bisect_right(starts, m.start()) - 1
        var = class_pos[k][1] if k >= 0 else None
        base = class_pos[k][3] if k >= 0 else "?"
        inner = class_pos[k][2] if k >= 0 else None
        pkg = base_scope.get(base) or scope.get(base) or "aiserver.v1."
        if base not in base_scope and base not in scope:
            unknown_bases[base] += 1
        if var is None:
            continue
        messages[var] = {"full": pkg + name, "pkg": pkg.rstrip("."),
                         "fields": parse_compact(compact, refs, var, inner)}

    # ---- 6. services
    services = []
    for m in re.finditer(r'typeName:"([^"]+)",methods:\{', s):
        svc = m.group(1)
        oi = m.end() - 1
        ej = balanced(s, oi)
        body = s[oi + 1:ej - 1]
        meths = []
        for mm in re.finditer(r'(\w+):\{name:"([^"]+)",I:([A-Za-z_$][\w$]*),O:([A-Za-z_$][\w$]*),kind:i\.(\w+)\}', body):
            meths.append({"js": mm.group(1), "name": mm.group(2),
                          "in": mm.group(3), "out": mm.group(4), "kind": mm.group(5)})
        services.append({"name": svc, "methods": meths})

    

    if parse_only:
        # hand the raw graph to diagnose_cycles.py before refs are resolved
        return messages, enums, services, explicit

    # ---- 7. resolve references
    # NOTE: we no longer fold types into another package to break cycles.
    # protoc only rejects *file-level* import cycles; a proto package may span
    # several files, and types inside one file may reference each other freely.
    # So cycles are broken by splitting packages into several files (section 8).

    def refs_of(ms):
        out = []
        for f in ms["fields"]:
            if f["kind"] == "ref": out.append(f["var"])
            elif f["kind"] == "map" and f["V"]["kind"] == "ref": out.append(f["V"]["var"])
        return out

    def pkg_of_full(full):
        segs = full.split(".")
        for i, sg in enumerate(segs):
            if sg[:1].isupper(): return ".".join(segs[:i])
        return ".".join(segs[:-1])

    def rel_of(full):
        segs = full.split(".")
        for i, sg in enumerate(segs):
            if sg[:1].isupper(): return ".".join(segs[i:])
        return segs[-1]

    def fam_of(full):          # "aiserver.v1.Outer.Inner" -> "aiserver.v1.Outer"
        segs = full.split(".")
        for i, sg in enumerate(segs):
            if sg[:1].isupper(): return ".".join(segs[:i + 1])
        return full

    full_of = {v: ms["full"] for v, ms in messages.items()}
    full_of.update({v: e["full"] for v, e in enums.items()})
    pkg_of = {v: ms["pkg"] for v, ms in messages.items()}
    pkg_of.update({v: pkg_of_full(e["full"]) for v, e in enums.items()})

    def fq(var):
        if var in full_of: return full_of[var]
        if var in explicit: return explicit[var]
        return None

    # capture the raw reference graph BEFORE refs are rewritten to type names
    nodes = list(messages) + [v for v in enums if v not in messages]
    edges = {v: [r for r in refs_of(ms) if r in full_of] for v, ms in messages.items()}
    for v in enums:
        edges.setdefault(v, [])

    unresolved = collections.Counter()
    for v, msg in messages.items():
        for f in msg["fields"]:
            if f["kind"] == "ref":
                t = fq(f["var"])
                if t is None: unresolved[f["var"]] += 1
                f.pop("var", None)
                f["kind"] = "named"; f["T"] = t or "string"
            elif f["kind"] == "map":
                vv = f["V"]
                if vv["kind"] == "ref":
                    t = fq(vv["var"])
                    if t is None: unresolved[vv["var"]] += 1
                    vv["kind"] = "named"; vv["T"] = t or "string"

    # ---- 8. break file-level cycles: SCC merge + topological segmentation
    def tarjan(nodes, adj):
        """Strongly-connected components (iterative Tarjan)."""
        index, low, onstk, stk, out = {}, {}, set(), [], []
        counter = [0]
        for root in nodes:
            if root in index: continue
            work = [(root, iter(adj.get(root, ())))]
            index[root] = low[root] = counter[0]; counter[0] += 1
            stk.append(root); onstk.add(root)
            while work:
                v, it = work[-1]
                for w in it:
                    if w not in index:
                        index[w] = low[w] = counter[0]; counter[0] += 1
                        stk.append(w); onstk.add(w)
                        work.append((w, iter(adj.get(w, ()))))
                        break
                    elif w in onstk:
                        low[v] = min(low[v], index[w])
                else:
                    work.pop()
                    if work: low[work[-1][0]] = min(low[work[-1][0]], low[v])
                    if low[v] == index[v]:
                        c = []
                        while True:
                            w = stk.pop(); onstk.discard(w); c.append(w)
                            if w == v: break
                        out.append(c)
        return out

    def merge_by(comp, key_of):
        """Union components that share a key (a nested type must sit next to its parent)."""
        parent = list(range(len(comp)))
        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]; a = parent[a]
            return a
        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb: parent[rb] = ra
        seen = {}
        for i, c in enumerate(comp):
            for x in c:
                k = key_of(x)
                if k in seen: union(seen[k], i)
                else: seen[k] = i
        g = collections.defaultdict(list)
        for i in range(len(comp)): g[find(i)].append(i)
        return [[x for i in v for x in comp[i]] for v in g.values()]

    comp = tarjan(nodes, edges)
    comp = merge_by(comp, lambda x: fam_of(full_of[x]))
    # merging components can create fresh cycles between them -> collapse again
    for _ in range(8):
        cid = {x: i for i, c in enumerate(comp) for x in c}
        adj2 = collections.defaultdict(set)
        for v, rs in edges.items():
            for r in rs:
                a, b = cid[v], cid[r]
                if a != b: adj2[a].add(b)
        n2 = tarjan(list(range(len(comp))), adj2)
        if len(n2) == len(comp): break
        comp = [[x for i in c for x in comp[i]] for c in n2]
        comp = merge_by(comp, lambda x: fam_of(full_of[x]))

    assert sum(len(c) for c in comp) == len(nodes), "SCC merge lost nodes"

    comp_id = {x: i for i, c in enumerate(comp) for x in c}
    comp_pkg = {}
    for i, c in enumerate(comp):
        ps = sorted({pkg_of[x] for x in c})
        if len(ps) > 1:
            print(f"  !! cycle spanning packages {ps}: {[full_of[x] for x in c][:4]}")
        comp_pkg[i] = ps[0]

    cadj = collections.defaultdict(set)
    indeg = collections.Counter({i: 0 for i in range(len(comp))})
    for v, rs in edges.items():
        for r in rs:
            a, b = comp_id[v], comp_id[r]
            if a != b and b not in cadj[a]:
                cadj[a].add(b); indeg[b] += 1

    # greedy topological order: keep emitting the current package while possible,
    # so a package only gets split where a real dependency cycle forces it
    ready = sorted(i for i in range(len(comp)) if indeg[i] == 0)
    segments, cur_pkg = [], None
    while ready:
        pick = next((i for i in ready if comp_pkg[i] == cur_pkg), None)
        if pick is None: pick = ready[0]
        ready.remove(pick)
        if comp_pkg[pick] != cur_pkg:
            cur_pkg = comp_pkg[pick]; segments.append([])
        segments[-1].append(pick)
        for w in sorted(cadj[pick]):
            indeg[w] -= 1
            if indeg[w] == 0: ready.append(w)
        ready.sort()

    seg_pos = {x: si for si, s in enumerate(segments) for i in s for x in comp[i]}
    back = [(full_of[v], full_of[r]) for v, rs in edges.items()
            for r in rs if seg_pos[v] > seg_pos[r]]
    print(f"  segmentation: {len(segments)} segments, backward cross-file edges: {len(back)}")
    for b in back[:6]: print("     BACK", b[0], "->", b[1])

    seg_file, seen_pkg = [], collections.Counter()
    for s in segments:
        p = comp_pkg[s[0]]; seen_pkg[p] += 1
        seg_file.append(p + ".proto" if seen_pkg[p] == 1 else f"{p}_{seen_pkg[p]}.proto")

    file_of = {}                       # var -> file
    for si, s in enumerate(segments):
        for i in s:
            for x in comp[i]: file_of[x] = seg_file[si]
    file_of_full = {full_of[v]: f for v, f in file_of.items()}

    # ---- 9. emit
    os.makedirs(out, exist_ok=True)
    WKT = {
        "google.protobuf.Struct": "google/protobuf/struct.proto",
        "google.protobuf.Value": "google/protobuf/struct.proto",
        "google.protobuf.ListValue": "google/protobuf/struct.proto",
        "google.protobuf.NullValue": "google/protobuf/struct.proto",
        "google.protobuf.Any": "google/protobuf/any.proto",
        "google.protobuf.Timestamp": "google/protobuf/timestamp.proto",
        "google.protobuf.Duration": "google/protobuf/duration.proto",
        "google.protobuf.Empty": "google/protobuf/empty.proto",
        "google.protobuf.FieldMask": "google/protobuf/field_mask.proto",
    }
    written = []

    def emit(fname, pkg, msgs, ens, svcs):
        imports = set()

        def note(name):
            if not name or "." not in name: return
            if name in WKT:
                imports.add(WKT[name]); return
            f = file_of_full.get(name)
            if f and f != fname: imports.add(f)

        tree = {}
        def walk(p):
            node = tree
            for seg in p.split("."):
                node = node.setdefault(seg, {})
            return node
        for p, vals in sorted(ens.items()): walk(p)["__enum__"] = vals
        for p, fields in sorted(msgs.items()): walk(p)["__fields__"] = fields

        def decl(f):
            k = f["kind"]
            if k == "map":
                note(f["V"].get("T"))
                return f"map<{SCALAR.get(f['K'], 'string')}, {f['V'].get('T')}> {f['name']} = {f['no']}"
            t = f.get("T")
            if k == "named": note(t)
            pre = "repeated " if f.get("repeated") else ("optional " if f.get("opt") else "")
            return f"{pre}{t} {f['name']} = {f['no']}"

        def render(node, indent):
            o = []
            pad = "  " * indent
            for k in sorted(node):
                if k.startswith("__"): continue
                child = node[k]
                if "__enum__" in child:
                    o.append(f"{pad}enum {k} {{")
                    for vn, no in child["__enum__"]:
                        o.append(f"{pad}  {vn} = {no};")
                    o.append(f"{pad}}}")
                    o.append("")
                    continue
                o.append(f"{pad}message {k} {{")
                fields = child.get("__fields__") or []
                oneofs = sorted({f["oneof"] for f in fields if f.get("oneof")})
                emitted = set()
                body = []
                for onf in oneofs:
                    body.append(f"{pad}  oneof {onf} {{")
                    for f in fields:
                        if f.get("oneof") != onf: continue
                        emitted.add(id(f))
                        body.append(f"{pad}    {decl(f)};")
                    body.append(f"{pad}  }}")
                for f in fields:
                    if id(f) in emitted: continue
                    body.append(f"{pad}  {decl(f)};")
                inner = render(child, indent + 1)
                o.extend(body)
                if inner:
                    if body: o.append("")
                    o.extend(inner)
                o.append(f"{pad}}}")
                o.append("")
            return o

        body = render(tree, 0)
        for svc in svcs:
            body.append(f"service {svc['name'].split('.')[-1]} {{")
            for mt in svc["methods"]:
                i = fq(mt["in"]) or "unknown"
                o_ = fq(mt["out"]) or "unknown"
                note(i); note(o_)
                kind = mt["kind"]
                if kind == "Unary":        sig = f"({i}) returns ({o_})"
                elif kind == "ServerStreaming": sig = f"({i}) returns (stream {o_})"
                elif kind == "ClientStreaming": sig = f"(stream {i}) returns ({o_})"
                else:                      sig = f"(stream {i}) returns (stream {o_})"
                body.append(f"  rpc {mt['name']}{sig};")
            body.append("}")
            body.append("")

        lines = ['syntax = "proto3";', f"package {pkg};", ""]
        for i in sorted(imports): lines.append(f'import "{i}";')
        if imports: lines.append("")
        lines.append(f"// {len(msgs)} messages, {len(ens)} enums, {len(svcs)} services"
                     f" -- reconstructed from {os.path.basename(src)}")
        lines.append("")
        lines.extend(body)
        path = os.path.join(out, fname)
        open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
        written.append(path)

    relocated = []
    for si, s in enumerate(segments):
        msgs, ens = {}, {}
        fpkg = comp_pkg[s[0]]
        for i in s:
            for x in comp[i]:
                if x in messages:  msgs[rel_of(full_of[x])] = messages[x]["fields"]
                elif x in enums:   ens[rel_of(full_of[x])] = enums[x]["values"]
                else:              continue
                # a type must stay in the package it was declared in; if the
                # segment's file package differs, the split is wrong
                if pkg_of.get(x) != fpkg:
                    relocated.append((full_of[x], pkg_of.get(x), fpkg))
        emit(seg_file[si], fpkg, msgs, ens, [])

    # services go into their own files: they are the graph's sinks, so they can
    # safely import every type file
    for svc in services:
        spkg = svc["name"].rpartition(".")[0]
        emit(f"svc_{svc['name']}.proto", spkg, {}, {}, [svc])

    for p in written:
        n = sum(1 for _ in open(p, encoding="utf-8"))
        print(f"  {os.path.basename(p):44s} {n:7d} lines")

    print(f"\nmessages={len(messages)} enums={len(enums)} services={len(services)} "
          f"packages={sorted(set(pkg_of.values()))}")
    print(f"files={len(written)}  (packages split into {len(segments)} segments "
          f"to keep file-level imports acyclic)")
    print(f"types relocated to another package: {len(relocated)}")
    for r in relocated[:10]: print("     RELOCATED", r[0], r[1], "->", r[2])
    print("unresolved refs:", sum(unresolved.values()), dict(unresolved.most_common(8)))
    print("unknown base classes:", dict(unknown_bases.most_common(10)))

    # 额外导出一份"源码级"描述符快照，供对拍脚本使用（不受运行时可达性限制）
    snap = {
        "messages": {full_of[v]: [
            {"no": f["no"], "name": f["name"], "kind": f["kind"],
             "T": f.get("T"), "repeated": bool(f.get("repeated")), "opt": bool(f.get("opt")),
             "K": f.get("K"), "V": (f.get("V") or {}).get("T") if f.get("V") else None}
            for f in ms["fields"]] for v, ms in messages.items()},
        "enums": {full_of[v]: [{"no": n, "name": nm} for nm, n in e["values"]]
                  for v, e in enums.items()},
        "services": {svc["name"]: [{"name": mt["name"], "I": fq(mt["in"]),
                                     "O": fq(mt["out"]), "kind": mt["kind"]}
                                    for mt in svc["methods"]] for svc in services},
    }
    sp = os.path.join(out, "snapshot.json")
    json.dump(snap, open(sp, "w", encoding="utf-8"))
    print("snapshot:", sp, f"{len(snap['messages'])} msgs / {len(snap['enums'])} enums "
                           f"/ {len(snap['services'])} svcs")


if __name__ == "__main__":
    main()
