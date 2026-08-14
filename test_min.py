import json
import urllib.request

TURSO_URL = "libsql://qualitytracking-quality-dashboard.aws-ap-northeast-1.turso.io"
TURSO_AUTH_TOKEN = "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9.eyJhIjoicnciLCJpYXQiOjE3ODY2Nzg4OTEsImlkIjoiMDE5ZmZkZmUtNTgwMS03YjQ2LWI3YzItMjY3NTlhZDI1MTAyIiwia2lkIjoiWDRuX2FBcVN0UW5halZoUUFWd2NSMUF5NXJHeTNRZ1ZmN0s1eVF4UlhWWSIsInJpZCI6ImE2ODAwMDZhLTRjZDYtNDRhMy1hMTM1LWM3YmYzNTAyMjNjZSJ9.RaofQcM2f-bkaxvJUvG37Rv42UrXcVvIrVyGtICBL02B7bmIuLB6Fsfd3u6zQFRkLWpe7_6ftUFWC1qeW7SRBw"

def get_http_url():
    url = TURSO_URL
    if url.startswith("libsql://"):
        url = "https://" + url[len("libsql://"):]
    if url.endswith("/"):
        url = url[:-1]
    return url + "/v2/pipeline"

# 最简单的建表语句
create_sql = "CREATE TABLE IF NOT EXISTS test_min (id INTEGER, name TEXT)"

# 最简单的插入语句
insert_sql = "INSERT INTO test_min (id, name) VALUES (?, ?)"

# 参数用 Turso API 要求的格式
args = [
    {"type": "integer", "value": "1"},
    {"type": "text", "value": "hello"},
]

def execute(sql, arg_list):
    body = {
        "requests": [
            {"type": "execute", "stmt": {"sql": sql, "args": arg_list}},
            {"type": "close"},
        ]
    }
    req = urllib.request.Request(
        get_http_url(),
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + TURSO_AUTH_TOKEN,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")

# 先建表
try:
    execute(create_sql, [])
    print("✅ 建表成功")
except Exception as e:
    print("❌ 建表失败:", e)
    raise SystemExit

# 再插入数据
try:
    result = execute(insert_sql, args)
    print("✅ 插入成功")
    print(result)
except Exception as e:
    print("❌ 插入失败:", e)