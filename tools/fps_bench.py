#!/usr/bin/env python3
"""On-device frame timing for the port, over adb.

  python3 tools/fps_bench.py --seconds 30

Reads SurfaceFlinger's frame timestamps for the game's own surface, which is
what a Unity game actually presents (dumpsys gfxinfo only sees HWUI, so it
reports nothing useful here). Prints fps and the frame-time percentiles that
tell you whether it is "slow" or "stuttering" -- on a weak phone those need
different fixes.
"""
import argparse
import statistics
import subprocess
import sys
import time

PKG = "com.Neznayaka.R.E.P.O"


def adb(*args: str) -> str:
    return subprocess.run(["adb", *args], capture_output=True, text=True, check=True).stdout


def find_layer(pkg: str) -> str:
    listing = adb("shell", "dumpsys", "SurfaceFlinger", "--list")
    hits = [ln.strip() for ln in listing.splitlines() if pkg in ln]
    if not hits:
        raise SystemExit(f"no SurfaceFlinger layer for {pkg}; is the game in the foreground?")
    return sorted(hits, key=len)[-1]


def parse_surfaceflinger_latency(text: str) -> dict:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    refresh_ns = int(lines[0])
    ts = []
    for ln in lines[1:]:
        parts = ln.split()
        if len(parts) != 3:
            continue
        present = int(parts[1])
        if present in (0, 9223372036854775807):        # dropped / pending frame
            continue
        ts.append(present)
    ts.sort()
    deltas = [(b - a) / 1e6 for a, b in zip(ts, ts[1:]) if b > a]
    if len(deltas) < 2:
        raise SystemExit("not enough frames captured -- is the game rendering?")
    refresh_ms = refresh_ns / 1e6
    jank = sum(1 for d in deltas if d > refresh_ms * 1.5)
    q = statistics.quantiles(deltas, n=100)
    return {
        "frames": len(deltas),
        "fps": round(1000.0 / statistics.fmean(deltas), 1),
        "p50_ms": round(statistics.median(deltas), 2),
        "p95_ms": round(q[94], 2),
        "p99_ms": round(q[98], 2),
        "worst_ms": round(max(deltas), 2),
        "jank_pct": round(100.0 * jank / len(deltas), 1),
        "refresh_ms": round(refresh_ms, 2),
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--package", default=PKG)
    ap.add_argument("--seconds", type=float, default=20)
    a = ap.parse_args(argv)

    layer = find_layer(a.package)
    print(f"layer: {layer}")
    adb("shell", "dumpsys", "SurfaceFlinger", "--latency-clear")
    print(f"recording {a.seconds:.0f}s -- play normally...")
    time.sleep(a.seconds)
    out = parse_surfaceflinger_latency(adb("shell", "dumpsys", "SurfaceFlinger", "--latency", layer))
    for k, v in out.items():
        print(f"  {k:>10}: {v}")
    # ponytail: no CPU/GPU counters. dumpsys gets you the number that decides
    # settings; per-frame profiling needs a Unity development build.
    return 0


if __name__ == "__main__":
    sys.exit(main())
