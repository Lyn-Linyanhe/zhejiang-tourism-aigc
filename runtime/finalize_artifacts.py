from pathlib import Path
import subprocess,json,os,hashlib
root=Path(r"C:\Users\ev041\Desktop\人工智能\zhejiang-tourism-aigc-merged")
changed=['.env.example','backend/app/config.py','backend/app/providers/image.py','backend/app/providers/video.py','backend/app/services/orchestrator.py','docs/upstream-integration.md','tests/test_tokendance_recovery.py']
(root/'MODIFIED_FILE').write_text('Integrated project: zhejiang-tourism-aigc-merged\nSource archive SHA256: 4DAD068088312ABFB049BE9E27FD11744CB951958BFC4B82C1F8F31DF4EA37B0\nChanged branch: strict AI first-frame -> image-to-video pipeline; TokenDance protocol dispatch, durable remote-task receipts, resume-safe polling, and no-music generation.\nChanged files:\n'+'\n'.join(changed)+'\n',encoding='utf-8')
diff=subprocess.run(['git','-C',str(root),'diff','--','.env.example','backend/app/config.py','backend/app/providers/image.py','backend/app/providers/video.py','backend/app/services/orchestrator.py','docs/upstream-integration.md'],capture_output=True,text=True,encoding='utf-8').stdout
newtest=(root/'tests/test_tokendance_recovery.py').read_text(encoding='utf-8')
diff+='\n--- /dev/null\n+++ b/tests/test_tokendance_recovery.py\n'+''.join('+'+line+'\n' for line in newtest.splitlines())
(root/'DIFF_FILE').write_text(diff,encoding='utf-8')
verification='''SOURCE_HASH: 4DAD068088312ABFB049BE9E27FD11744CB951958BFC4B82C1F8F31DF4EA37B0
CHANGED_BRANCH: strict AI first-frame -> image-to-video pipeline; TokenDance protocol dispatch, durable remote-task receipts, resume-safe polling, and no-music generation
ARTIFACTS:
- C:\\Users\\ev041\\Desktop\\人工智能\\zhejiang-tourism-aigc-merged\\MODIFIED_FILE
- C:\\Users\\ev041\\Desktop\\人工智能\\zhejiang-tourism-aigc-merged\\DIFF_FILE
- C:\\Users\\ev041\\Desktop\\人工智能\\zhejiang-tourism-aigc-merged\\VERIFICATION.txt
- C:\\Users\\ev041\\Desktop\\人工智能\\zhejiang-tourism-aigc-merged\\ROLLBACK.sh

BASELINE_COMMAND: python -m unittest tests.test_tokendance_recovery -q
BASELINE_INPUT: simulated TokenDance submit success followed by query failure, then retry for the same shot
BASELINE_LITERAL_RESULT: FAILED (failures=2); receipt missing and retry method was POST instead of GET
BASELINE_EXIT_STATUS: 1

MODIFIED_COMMAND: python -m compileall -q backend tests; node --check frontend/app.js; python -m unittest discover -s tests -q; powershell -ExecutionPolicy Bypass -File scripts/smoke-test.ps1
MODIFIED_INPUT: 37 unit tests plus offline 15-second 720x1280 end-to-end video render
MODIFIED_LITERAL_RESULT: Ran 37 tests in 7.106s / OK; SMOKE TEST PASSED: runtime-smoke\\media\\task_7c5133ef664c\\final.mp4
MODIFIED_EXIT_STATUS: compile=0 node=0 unittest=0 smoke=0

REAL_GENERATION_COMMAND: runtime\\run_generation_attempt.py against http://127.0.0.1:8787
REAL_GENERATION_INPUT: Hangzhou West Lake, 15 seconds, 4 shots, asset_source=ai, music_mood=无音乐
REAL_GENERATION_LITERAL_RESULT: preview draft_1da5669ff82c created; system voice.wav and assets\\shot_1.png created; after 30 polls/150 seconds status remained running, stage=assets, progress=67, assets=0; server stopped and task marked failed
REAL_GENERATION_EXIT_STATUS: polling script=0; application task=failed after controlled pause

ROLLBACK_COMMAND: bash -n ROLLBACK.sh; bash ROLLBACK.sh (executed only inside runtime\\rollback-test-20261007)
ROLLBACK_INPUT: copied modified tree plus integration-backup-20261006
ROLLBACK_LITERAL_RESULT: rollback_test=restored all five backup files; test added file removed; exit=0
ROLLBACK_EXIT_STATUS: 0
RESTORED_BEHAVIOR_STATUS: rollback test copy hashes match pre-change backups for config, image, video, registry, and orchestrator; live MODIFIED_FILE remains changed.
'''
(root/'VERIFICATION.txt').write_text(verification,encoding='utf-8')
for f in ['MODIFIED_FILE','DIFF_FILE','VERIFICATION.txt','ROLLBACK.sh']:
 p=root/f; print(f,p.stat().st_size,hashlib.sha256(p.read_bytes()).hexdigest())
