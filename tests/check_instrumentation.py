import sqlite3

con = sqlite3.connect("recruitment.db")
cur = con.cursor()

print("=== 埋点落库确认 ===")
n, cost, tok_in, tok_out = cur.execute(
    "SELECT COUNT(*), COALESCE(SUM(cost),0), "
    "COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0) FROM llm_call_logs"
).fetchone()
print(f"  总调用 {n} 次")
print(f"  累计 token: 入 {tok_in} / 出 {tok_out}")
print(f"  累计成本: ${cost}")

print()
print("=== 按调用环节分布（验证 call_site 生效）===")
rows = cur.execute(
    "SELECT call_site, COUNT(*), COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0) "
    "FROM llm_call_logs GROUP BY call_site ORDER BY COUNT(*) DESC"
).fetchall()
for site, cnt, ti, to in rows:
    print(f"  {site:<30} {cnt:>4} 次   token {ti}/{to}")

print()
print("=== 状态分布（验证降级与失败可区分）===")
rows = cur.execute(
    "SELECT status, degraded, COUNT(*) FROM llm_call_logs GROUP BY status, degraded"
).fetchall()
for status, degraded, cnt in rows:
    print(f"  status={status:<16} degraded={bool(degraded)!s:<6} {cnt} 次")

print()
print("=== token 用量缺失情况（验证 usage_missing 兜底）===")
miss = cur.execute("SELECT COUNT(*) FROM llm_call_logs WHERE usage_missing = 1").fetchone()[0]
print(f"  未返回用量: {miss} / {n}")

con.close()
