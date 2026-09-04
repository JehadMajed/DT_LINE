#!/usr/bin/env python3
"""Poll go2rtc consumers every 30 s and record the transport mix.

Read-only. Answers "how many researchers actually benefit" with real numbers
instead of a pair of snapshots of the same one or two people. One JSONL line
per poll; a consumer is counted once per poll it is present in, so the file is
a time series of concurrent viewers, not a count of distinct people.
"""
import json
import time
import urllib.request

OUT = "/home/jehadroot/DT_LINE/archive/data/transport_poll.jsonl"
URL = "http://127.0.0.1:1984/api/streams"
INTERVAL = 30


def family(addr):
    """Classify a consumer's remote address."""
    a = str(addr or "")
    if a.startswith("127.0.0.1") or "forwarded" in a:
        return "funnel"          # tailscale serve proxies from localhost
    if a.startswith("["):
        return "ipv6"
    if a.startswith("100."):
        return "tailnet"
    if a:
        return "ipv4"
    return "unknown"


while True:
    rec = {"ts": int(time.time() * 1000)}
    try:
        with urllib.request.urlopen(URL, timeout=10) as r:
            data = json.load(r)
        cons = []
        for name, s in data.items():
            for c in (s.get("consumers") or []):
                cons.append({
                    "stream": name,
                    "format": c.get("format_name"),
                    "protocol": c.get("protocol"),
                    "family": family(c.get("remote_addr")),
                    "remote": str(c.get("remote_addr"))[:60],
                    "bytes_send": c.get("bytes_send"),
                })
        rec["n"] = len(cons)
        rec["consumers"] = cons
        rec["producer_up"] = any(s.get("producers") for s in data.values())
    except Exception as e:
        rec["error"] = "%s: %s" % (type(e).__name__, e)
    with open(OUT, "a") as f:
        f.write(json.dumps(rec) + "\n")
    time.sleep(INTERVAL)
