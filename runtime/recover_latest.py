import sqlite3,json
from datetime import datetime,timezone
from pathlib import Path
p=Path(r"C:\Users\ev041\Desktop\人工智能\zhejiang-tourism-aigc-merged\runtime\db\app.sqlite3")
c=sqlite3.connect(p)
for tid in ("draft_1da5669ff82c",):
 row=c.execute("select status,payload from tasks where id=?",(tid,)).fetchone()
 if row and row[0]=="running":
  obj=json.loads(row[1]); obj["status"]="failed"; obj["stage"]="assets"; obj["progress"]=67; obj["error"]="Generation paused after cloud image-to-video produced no response within the verification window; no final video was produced."
  obj.setdefault("trace",{}).setdefault("events",[]).append({"type":"error","stage":"assets","message":obj["error"]})
  now=datetime.now(timezone.utc).isoformat(); c.execute("update tasks set status=?,payload=?,updated_at=? where id=?",("failed",json.dumps(obj,ensure_ascii=False),now,tid)); print(tid,'recovered')
c.commit()
