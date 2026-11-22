import sqlite3,json
from datetime import datetime,timezone
from pathlib import Path
p=Path(r"C:\Users\ev041\Desktop\人工智能\zhejiang-tourism-aigc-merged\runtime\db\app.sqlite3")
c=sqlite3.connect(p)
row=c.execute("select status,payload from tasks where id=?",("draft_a88812a04bfb",)).fetchone()
if row and row[0]=="running":
    obj=json.loads(row[1]); obj["status"]="failed"; obj["stage"]="assets"; obj["progress"]=67
    obj["error"]="Previous generation was interrupted while waiting for cloud image-to-video; no final video was produced."
    trace=obj.setdefault("trace",{}); events=trace.setdefault("events",[]); events.append({"type":"error","stage":"assets","message":obj["error"]})
    now=datetime.now(timezone.utc).isoformat()
    c.execute("update tasks set status=?,payload=?,updated_at=? where id=?",("failed",json.dumps(obj,ensure_ascii=False),now,"draft_a88812a04bfb")); c.commit(); print('recovered stale task as failed')
else: print('no running stale task to recover')
