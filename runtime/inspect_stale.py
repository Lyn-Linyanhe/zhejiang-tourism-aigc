import sqlite3,json
from pathlib import Path
p=Path(r"C:\Users\ev041\Desktop\人工智能\zhejiang-tourism-aigc-merged\runtime\db\app.sqlite3")
c=sqlite3.connect(p)
row=c.execute("select status,payload,updated_at from tasks where id=?",("draft_a88812a04bfb",)).fetchone()
if not row: print('missing'); raise SystemExit
status,payload,updated=row
obj=json.loads(payload)
print(json.dumps({'db_status':status,'updated_at':updated,'payload_status':obj.get('status'),'stage':obj.get('stage'),'progress':obj.get('progress'),'assets':len(obj.get('assets') or []),'events':obj.get('trace',{}).get('events',[])},ensure_ascii=False,indent=2)[:12000])
