import json,urllib.request,urllib.error,time
from pathlib import Path
base='http://127.0.0.1:8787'
root=Path(__file__).resolve().parent
body={"city":"杭州","landmark":"西湖","theme":"西湖春日宋韵游","culture":"宋韵文化与江南园林","festival":"春日","audience":"年轻游客","duration":15,"style":"诗意纪实","voice":"女声","music_mood":"无音乐","shot_count":4,"asset_source":"ai","output_resolution":"720x1280","fps":25,"custom_brief":"必须使用AI生成首帧并进行图生视频，生成有镜头运动的连续视频；不使用音乐。"}
def call(path,method='POST',data=None):
    req=urllib.request.Request(base+path,data=(json.dumps(data,ensure_ascii=False).encode() if data is not None else None),headers={'Content-Type':'application/json'},method=method)
    try:
        with urllib.request.urlopen(req,timeout=120) as r: return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print('HTTP',e.code,e.read().decode()); raise
preview=call('/api/previews',data=body)
tid=preview.get('id'); (root/'latest-attempt-id.txt').write_text(tid,encoding='utf-8')
print(json.dumps({'preview_id':tid,'status':preview.get('status'),'shot_count':len(preview.get('shots') or [])},ensure_ascii=False),flush=True)
res=call(f'/api/tasks/{tid}/generate',data={}); print(json.dumps({'generate_status':res.get('status'),'task_id':res.get('id')},ensure_ascii=False),flush=True)
for i in range(30):
    time.sleep(5)
    obj=call(f'/api/tasks/{tid}','GET'); (root/'latest-attempt.json').write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'i':i+1,'status':obj.get('status'),'stage':obj.get('stage'),'progress':obj.get('progress'),'assets':len(obj.get('assets') or []),'error':obj.get('error')},ensure_ascii=False),flush=True)
    if obj.get('status') in {'completed','failed','cancelled'}: break
