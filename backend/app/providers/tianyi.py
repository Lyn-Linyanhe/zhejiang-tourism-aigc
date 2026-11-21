"""Local community Tianyi voice, loaded on demand in an isolated process.

No HTTP model server or X Studio automation is involved. The child exits after
an idle interval so GPU memory is available to the video workflow again.
"""
from __future__ import annotations
import atexit
import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MODEL_ID = "tianyi-community-v2pro-cb5fed8d-1bd07ccc-v1"
VOICE_NAME = "天依·本地社区音色"
MARKER = "TIANYI_RESULT:"


def runtime_paths() -> tuple[Path, Path]:
    return PROJECT_ROOT / ".venv-tianyi/Scripts/python.exe", PROJECT_ROOT / "runtime/tianyi"


def availability() -> dict[str, Any]:
    python, home = runtime_paths()
    required = [python, home/"manifest.json", home/"weights/tianyi2-e15.ckpt",
                home/"engine/GPT_SoVITS/TTS_infer_pack/TTS.py",
                home/"weights/tianyi2_e8_s128.pth", home/"community-model/ref/ref.wav",
                home/"community-model/ref/prompt.txt",
                home/"engine/GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large/pytorch_model.bin",
                home/"engine/GPT_SoVITS/pretrained_models/chinese-hubert-base/pytorch_model.bin",
                home/"engine/GPT_SoVITS/pretrained_models/sv/pretrained_eres2netv2w24s4ep4.ckpt",
                home/"engine/GPT_SoVITS/text/G2PWModel/g2pW.onnx",
                home/"nltk_data/corpora/cmudict.zip",
                home/"nltk_data/taggers/averaged_perceptron_tagger.zip",
                home/"nltk_data/taggers/averaged_perceptron_tagger_eng/averaged_perceptron_tagger_eng.weights.json",
                home/"installed.json"]
    base = home/"engine/GPT_SoVITS/pretrained_models"
    required += [base/name for name in (
        "chinese-roberta-wwm-ext-large/config.json", "chinese-roberta-wwm-ext-large/tokenizer.json",
        "chinese-hubert-base/config.json", "chinese-hubert-base/preprocessor_config.json")]
    missing = [str(p.relative_to(PROJECT_ROOT)) for p in required if not p.is_file()]
    return {"available":not missing, "missing":missing, "model":MODEL_ID,
            "voice":VOICE_NAME, "source":"community", "offline":True,
            "pitch_control":False, "source_url":"https://github.com/Kevin14827/LuoTianyi-TTS"}


class TianyiRuntime:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.process: subprocess.Popen | None = None
        self.responses: queue.Queue = queue.Queue()
        self.timer: threading.Timer | None = None
        self.log = None
        atexit.register(self.close)

    def _start(self) -> None:
        if self.process and self.process.poll() is None:
            return
        self.close()
        status = availability()
        if not status["available"]:
            raise RuntimeError("天依本地声库尚未安装完整，请运行 scripts/setup-tianyi.ps1；缺少：" + ", ".join(status["missing"]))
        python, home = runtime_paths()
        environment = os.environ.copy()
        environment.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
        self.log = (home/"inference.log").open("a",encoding="utf-8")
        process = subprocess.Popen([str(python), "-u", str(Path(__file__).with_name("tianyi_worker.py"))],
            cwd=PROJECT_ROOT, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self.log, text=True, encoding="utf-8", bufsize=1,
            creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        self.process = process
        responses = queue.Queue()
        self.responses = responses
        log = self.log
        def read_output() -> None:
            try:
                for line in process.stdout:
                    if line.startswith(MARKER):
                        responses.put(json.loads(line[len(MARKER):]))
                    elif not log.closed:
                        log.write(line); log.flush()
            except Exception as exc:
                responses.put({"ok":False,"error":str(exc)})
            finally:
                responses.put({"ok":False,"error":"本地声库进程已退出，详见 runtime/tianyi/inference.log"})
        threading.Thread(target=read_output,daemon=True).start()

    def synthesize(self, text: str, output_path: Path, rate: float) -> dict[str, Any]:
        if not text.strip() or len(text)>1000:
            raise ValueError("天依单段文本需为 1–1000 字")
        if not 0.5 <= float(rate) <= 2:
            raise ValueError("天依语速需在 0.5–2 之间")
        with self.lock:
            if self.timer:
                self.timer.cancel()
            self._start()
            started=time.monotonic()
            try:
                self.process.stdin.write(json.dumps({"text":text,"output_path":str(output_path.resolve()),"rate":rate},ensure_ascii=False)+"\n")
                self.process.stdin.flush()
                response=self.responses.get(timeout=240)
                if not response.get("ok"):
                    raise RuntimeError(str(response.get("error","本地合成失败")))
                response["elapsed_seconds"]=round(time.monotonic()-started,3)
                return response
            except queue.Empty:
                self.close()
                raise RuntimeError("天依本地合成超过 240 秒，请检查 runtime/tianyi/inference.log") from None
            except Exception:
                self.close()
                raise
            finally:
                if self.process and self.process.poll() is None:
                    self.timer=threading.Timer(90,self.close)
                    self.timer.daemon=True
                    self.timer.start()

    def close(self) -> None:
        with self.lock:
            if self.timer:
                self.timer.cancel(); self.timer=None
            if self.process:
                process=self.process; self.process=None
                if process.poll() is None:
                    process.terminate()
                    try:process.wait(timeout=5)
                    except subprocess.TimeoutExpired:process.kill();process.wait()
                if process.stdin:process.stdin.close()
            if self.log:
                self.log.close(); self.log=None


runtime = TianyiRuntime()
