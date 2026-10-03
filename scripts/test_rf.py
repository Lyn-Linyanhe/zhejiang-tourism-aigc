# -*- coding: utf-8 -*-
"""定位 403：response_format 参数 A/B 测试"""
import json
import urllib.request

env = {}
for line in open(".env", encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        env[k] = v


def call(with_rf):
    body = {
        "model": env["LLM_MODEL"],
        "messages": [
            {"role": "system", "content": "你是严谨的编导，只输出合法 JSON。"},
            {"role": "user", "content": '输出 {"title":"字符串"}，title 为"杭州"两个字。'},
        ],
    }
    if with_rf:
        body["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(
        env["LLM_BASE_URL"] + "/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + env["LLM_API_KEY"],
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            p = json.loads(r.read().decode("utf-8"))
        return "OK: " + p["choices"][0]["message"]["content"][:80]
    except Exception as e:
        return "失败: {} {}".format(type(e).__name__, str(e)[:200])


print("带 response_format:", call(True))
print("不带 response_format:", call(False))
