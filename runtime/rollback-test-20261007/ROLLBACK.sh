#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKUP="$ROOT/runtime/integration-backup-20261006"
for f in backend/app/config.py backend/app/providers/image.py backend/app/providers/video.py backend/app/providers/registry.py backend/app/services/orchestrator.py; do
  mkdir -p "$ROOT/$(dirname "$f")"
  cp "$BACKUP/$f" "$ROOT/$f"
done
rm -f "$ROOT/tests/test_tokendance_recovery.py"
echo "rollback copy restored; rerun compile/tests to verify"
