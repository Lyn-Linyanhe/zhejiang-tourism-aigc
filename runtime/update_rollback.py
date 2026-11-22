from pathlib import Path
import subprocess, shutil, os
root=Path(r"C:\Users\ev041\Desktop\人工智能\zhejiang-tourism-aigc-merged")
backup=root/'runtime/integration-backup-20261006/backend/app/services'; backup.mkdir(parents=True,exist_ok=True)
content=subprocess.check_output(['git','-C',str(root),'show','HEAD:backend/app/services/orchestrator.py'])
(backup/'orchestrator.py').write_bytes(content)
rollback=root/'ROLLBACK.sh'
rollback.write_text('''#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKUP="$ROOT/runtime/integration-backup-20261006"
for f in backend/app/config.py backend/app/providers/image.py backend/app/providers/video.py backend/app/providers/registry.py backend/app/services/orchestrator.py; do
  mkdir -p "$ROOT/$(dirname "$f")"
  cp "$BACKUP/$f" "$ROOT/$f"
done
rm -f "$ROOT/tests/test_tokendance_recovery.py"
echo "rollback copy restored; rerun compile/tests to verify"
''',encoding='utf-8',newline='\n')
os.chmod(rollback,0o755)
print('backup and rollback updated')
