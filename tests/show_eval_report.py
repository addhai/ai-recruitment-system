import json
import os
import sys

p = sys.argv[1]
r = json.load(open(p, encoding="utf-8"))
s = r["summary"]
print(f"评测: {s['passed']}/{s['cases']} 通过 (repeat={s['repeat']})")
print("决策分布:", s["decisions"])
print("数值分布(仅记录):")
for k, d in s["score_distribution"].items():
    print(f"  {k:12} n={d['n']:3} min={d['min']:>5} median={d['median']:>6} max={d['max']:>5}")
