#!/usr/bin/env python3
"""Summarise the transport poller log.

Run any time:  python3 ~/DT_LINE/archive/tools/transport_summary.py

Reports the transport mix by format and address family, and answers the one
question the STUN change hinges on: has ANY consumer appeared with an IPv4
srflx candidate? Deliberately reports raw counts and distinct remote addresses
rather than percentages -- a handful of samples of the same one or two people
is not a sample, and a fraction would imply more than the data supports.
"""
import json
import sys
import time
from collections import Counter

PATH = sys.argv[1] if len(sys.argv) > 1 else \
    "/home/jehadroot/DT_LINE/archive/data/transport_poll.jsonl"

rows = []
for line in open(PATH):
    line = line.strip()
    if line:
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass

if not rows:
    print("no polls recorded yet")
    sys.exit(0)

t0, t1 = rows[0]["ts"] / 1000, rows[-1]["ts"] / 1000
hours = (t1 - t0) / 3600.0
errs = sum(1 for r in rows if r.get("error"))
print("polls        : %d over %.1f h  (%s -> %s)" % (
    len(rows), hours,
    time.strftime("%Y-%m-%d %H:%M", time.localtime(t0)),
    time.strftime("%Y-%m-%d %H:%M", time.localtime(t1))))
print("poll errors  : %d" % errs)
print("producer up  : %d/%d polls" % (sum(1 for r in rows if r.get("producer_up")), len(rows)))

fmt = Counter()
fam = Counter()
pair = Counter()
remotes = {}
srflx_v4 = []
concurrent = Counter()

for r in rows:
    cs = r.get("consumers") or []
    concurrent[len(cs)] += 1
    for c in cs:
        f = c.get("format") or "?"
        a = c.get("family") or "?"
        fmt[f] += 1
        fam[a] += 1
        pair[(f, a)] += 1
        rem = c.get("remote") or ""
        key = rem.split(" ")[0]
        remotes.setdefault(key, {"format": f, "family": a, "polls": 0})
        remotes[key]["polls"] += 1
        # An IPv4 srflx candidate is the proof the STUN change did real work.
        if "srflx" in rem and not rem.startswith("["):
            srflx_v4.append(rem)

print()
print("consumer-polls by format   :", dict(fmt))
print("consumer-polls by family   :", dict(fam))
print("format x family            :", {"%s/%s" % k: v for k, v in pair.items()})
print("concurrent viewers histogram (viewers: polls):", dict(sorted(concurrent.items())))

print()
print("distinct remote endpoints seen: %d" % len(remotes))
for k, v in sorted(remotes.items(), key=lambda x: -x[1]["polls"])[:15]:
    print("   %-52s %-10s %-8s %4d polls" % (k[:52], v["format"], v["family"], v["polls"]))

print()
print("=" * 68)
if srflx_v4:
    print("IPv4 srflx consumers seen: %d  <-- STUN change did real work" % len(srflx_v4))
    for s in srflx_v4[:5]:
        print("   ", s)
else:
    print("IPv4 srflx consumers seen: NONE")
    print("  The public IPv4 candidate is advertised, but no viewer has used it.")
    print("  That is not yet evidence the change failed -- it may simply mean no")
    print("  IPv4-only viewer has connected. Check the family counts above.")
print("=" * 68)
