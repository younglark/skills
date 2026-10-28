#!/usr/bin/env python3
"""Diff two snapshot.json files (old vs new) produced by extract_proto.py.

Answers the question every version bump raises: what did they add, remove,
or change?  Snapshots are source-level, so this also covers types that are
defined in proto.cjs but never reachable at runtime.

Usage: python3 diff_snapshot.py OLD/snapshot.json NEW/snapshot.json [-o report.md]
"""
import argparse
import collections
import json
import sys


def load(p):
    return json.load(open(p, encoding="utf-8"))


def sig_field(f):
    t = f.get("T")
    if f.get("kind") == "map":
        t = f"map<{f.get('K')},{f.get('V')}>"
    return f"{f['no']}:{f['name']}:{t}" + ("*" if f.get("repeated") else "") + \
           ("?" if f.get("opt") else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("-o", "--out", help="also write a markdown report here")
    args = ap.parse_args()

    o, n = load(args.old), load(args.new)
    lines = []

    def emit(s=""):
        print(s)
        lines.append(s)

    om, nm = o["messages"], n["messages"]
    oe, ne = o["enums"], n["enums"]
    os_, ns = o["services"], n["services"]

    add_m = sorted(set(nm) - set(om))
    del_m = sorted(set(om) - set(nm))
    chg_m = []
    for k in sorted(set(om) & set(nm)):
        fo = {f["no"]: f for f in om[k]}
        fn = {f["no"]: f for f in nm[k]}
        d = []
        for no in sorted(set(fo) | set(fn)):
            a, b = fo.get(no), fn.get(no)
            if a is None:      d.append(f"+{sig_field(b)}")
            elif b is None:    d.append(f"-{sig_field(a)}")
            elif sig_field(a) != sig_field(b):
                d.append(f"~{sig_field(a)} -> {sig_field(b)}")
        if d: chg_m.append((k, d))

    emit("=" * 68)
    emit(f"messages  {len(om)} -> {len(nm)}   (+{len(add_m)} -{len(del_m)} ~{len(chg_m)})")
    emit(f"enums     {len(oe)} -> {len(ne)}")
    emit(f"services  {len(os_)} -> {len(ns)}")
    emit("=" * 68)

    emit("\n--- new messages ---")
    for k in add_m[:40]: emit(f"  + {k}")
    if len(add_m) > 40: emit(f"  ... and {len(add_m) - 40} more")

    emit("\n--- removed messages ---")
    for k in del_m[:40]: emit(f"  - {k}")
    if len(del_m) > 40: emit(f"  ... and {len(del_m) - 40} more")

    emit("\n--- changed messages ---")
    for k, d in chg_m[:40]:
        emit(f"  ~ {k}")
        for x in d[:6]: emit(f"      {x}")
        if len(d) > 6: emit(f"      ... {len(d) - 6} more field diffs")
    if len(chg_m) > 40: emit(f"  ... and {len(chg_m) - 40} more")

    emit("\n--- enum changes ---")
    ec = 0
    for k in sorted(set(oe) & set(ne)):
        a = {v["no"]: v["name"] for v in oe[k]}
        b = {v["no"]: v["name"] for v in ne[k]}
        if a != b:
            ec += 1
            if ec <= 20:
                emit(f"  ~ {k}")
                for no in sorted(set(a) | set(b)):
                    if a.get(no) != b.get(no):
                        emit(f"      {no}: {a.get(no)} -> {b.get(no)}")
    emit(f"  ({ec} enums changed, +{len(set(ne) - set(oe))} new, "
         f"-{len(set(oe) - set(ne))} removed)")

    emit("\n--- service / RPC changes ---")
    tot_o = sum(len(v) for v in os_.values())
    tot_n = sum(len(v) for v in ns.values())
    emit(f"  methods {tot_o} -> {tot_n}")
    for svc in sorted(set(os_) | set(ns)):
        a = {m["name"]: m for m in os_.get(svc, [])}
        b = {m["name"]: m for m in ns.get(svc, [])}
        new = sorted(set(b) - set(a)); gone = sorted(set(a) - set(b))
        sig_ch = [k for k in sorted(set(a) & set(b))
                  if (a[k]["I"], a[k]["O"], a[k]["kind"]) !=
                     (b[k]["I"], b[k]["O"], b[k]["kind"])]
        if not (new or gone or sig_ch): continue
        emit(f"\n  {svc}")
        for k in new:  emit(f"    + {k}({b[k]['I']}) -> {b[k]['O']} [{b[k]['kind']}]")
        for k in gone: emit(f"    - {k}")
        for k in sig_ch:
            emit(f"    ~ {k}: ({a[k]['I']}->{a[k]['O']} {a[k]['kind']}) -> "
                 f"({b[k]['I']}->{b[k]['O']} {b[k]['kind']})")

    if args.out:
        open(args.out, "w", encoding="utf-8").write("\n".join(lines) + "\n")
        print(f"\nreport: {args.out}")


if __name__ == "__main__":
    main()
