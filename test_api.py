import urllib.request
import urllib.parse
import json

# 先登录获取token
data = urllib.parse.urlencode({'username': 'admin', 'password': 'admin123'}).encode()
req = urllib.request.Request('http://localhost:8000/auth/login', data=data)
req.add_header('Content-Type', 'application/x-www-form-urlencoded')
r = urllib.request.urlopen(req)
result = json.loads(r.read())
token = result['access_token']
print('登录成功!')

# 测试获取候选人列表
req = urllib.request.Request('http://localhost:8000/candidates/')
req.add_header('Authorization', 'Bearer ' + token)
r = urllib.request.urlopen(req)
candidates = json.loads(r.read())
print('候选人数量:', len(candidates))
for c in candidates[:3]:
    print('  -', c['name'], ':', c['position'], '(', c['status'], ')')

# 测试获取仪表盘统计
req = urllib.request.Request('http://localhost:8000/dashboard/stats')
req.add_header('Authorization', 'Bearer ' + token)
r = urllib.request.urlopen(req)
stats = json.loads(r.read())
print()
print('仪表盘统计:')
print('  总候选人:', stats['total_candidates'])
print('  待处理:', stats['pending_candidates'])
print('  已录用:', stats['hired_candidates'])
print('  平均匹配度:', round(stats['avg_match_score'], 1))

# 测试获取面试列表
req = urllib.request.Request('http://localhost:8000/interviews/')
req.add_header('Authorization', 'Bearer ' + token)
r = urllib.request.urlopen(req)
interviews = json.loads(r.read())
print()
print('面试数量:', len(interviews))

# 测试获取问卷列表
req = urllib.request.Request('http://localhost:8000/questionnaires/')
req.add_header('Authorization', 'Bearer ' + token)
r = urllib.request.urlopen(req)
questionnaires = json.loads(r.read())
print('问卷数量:', len(questionnaires))
for q in questionnaires:
    print('  -', q['name'], '(', q['type'], ')')

print()
print('所有API测试通过!')
