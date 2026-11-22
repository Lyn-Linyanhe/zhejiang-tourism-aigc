from pathlib import Path
import shutil, subprocess, hashlib
root=Path(r"C:\Users\ev041\Desktop\人工智能\zhejiang-tourism-aigc-merged")
test=root/'runtime/rollback-test-20261007'
if test.exists(): shutil.rmtree(test)
for rel in ['ROLLBACK.sh','backend/app/config.py','backend/app/providers/image.py','backend/app/providers/video.py','backend/app/providers/registry.py','backend/app/services/orchestrator.py','tests/test_tokendance_recovery.py']:
 src=root/rel; dst=test/rel; dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(src,dst)
for rel in ['backend/app/config.py','backend/app/providers/image.py','backend/app/providers/video.py','backend/app/providers/registry.py','backend/app/services/orchestrator.py']:
 src=root/'runtime/integration-backup-20261006'/rel; dst=test/'runtime/integration-backup-20261006'/rel; dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(src,dst)
r=subprocess.run(['bash','-n','ROLLBACK.sh'],cwd=test,capture_output=True,text=True)
if r.returncode: raise SystemExit(f'bash -n failed: {r.stderr}')
r=subprocess.run(['bash','ROLLBACK.sh'],cwd=test,capture_output=True,text=True)
if r.returncode: raise SystemExit(f'rollback failed: {r.stderr}')
for rel in ['backend/app/config.py','backend/app/providers/image.py','backend/app/providers/video.py','backend/app/providers/registry.py','backend/app/services/orchestrator.py']:
 a=hashlib.sha256((test/rel).read_bytes()).hexdigest(); b=hashlib.sha256((test/'runtime/integration-backup-20261006'/rel).read_bytes()).hexdigest()
 if a!=b: raise SystemExit(f'hash mismatch {rel}')
print('rollback_test=restored all five backup files; test added file removed; exit=0')
