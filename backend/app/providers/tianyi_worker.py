"""JSON-lines inference worker; launched only by the local provider."""
from __future__ import annotations
import json
import os
import subprocess
import sys
import traceback
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HOME = ROOT/"runtime/tianyi"
ENGINE = HOME/"engine"
MARKER = "TIANYI_RESULT:"


def load_reference_audio(filename, sample_rate):
    """The engine's shared utility imports its web UI; only decoding is needed here."""
    import numpy as np
    decoded = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(filename), "-f", "f32le",
         "-acodec", "pcm_f32le", "-ac", "1", "-ar", str(sample_rate), "pipe:1"],
        capture_output=True, check=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return np.frombuffer(decoded.stdout, dtype=np.float32).copy()


def load_engine():
    os.chdir(ENGINE)
    sys.path[:0]=[str(ENGINE),str(ENGINE/"GPT_SoVITS")]
    os.environ.update(language="zh_CN", version="v2Pro", GSV_NODDP="1", NLTK_DATA=str(HOME/"nltk_data"))
    import torch
    torch.set_num_threads(4)
    # Same jieba API/dictionaries without requiring a Windows C++ compiler.
    import jieba
    import jieba.posseg
    sys.modules["jieba_fast"] = jieba
    sys.modules["jieba_fast.posseg"] = jieba.posseg
    audio_utils = types.ModuleType("tools.my_utils")
    audio_utils.load_audio = load_reference_audio
    sys.modules["tools.my_utils"] = audio_utils
    from TTS_infer_pack.TTS import TTS, TTS_Config
    # Mixed Chinese/Latin phrases must not trigger a language-model download.
    import fast_langdetect
    fast_langdetect.infer._default_detector = fast_langdetect.infer.LangDetector(
        fast_langdetect.infer.LangDetectConfig(
            custom_model_path=str(Path(fast_langdetect.__file__).parent/"resources/lid.176.ftz"),
            allow_fallback=False))
    device="cuda" if torch.cuda.is_available() else "cpu"
    config={"custom":{
        "bert_base_path":"GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large",
        "cnhuhbert_base_path":"GPT_SoVITS/pretrained_models/chinese-hubert-base",
        "device":device,"is_half":device=="cuda","version":"v2Pro",
        "t2s_weights_path":str(HOME/"weights/tianyi2-e15.ckpt"),
        "vits_weights_path":str(HOME/"weights/tianyi2_e8_s128.pth"),
    }}
    return TTS(TTS_Config(config)), device


def main() -> None:
    engine=None
    for line in sys.stdin:
        try:
            request=json.loads(line)
            if engine is None:engine,device=load_engine()
            import numpy as np
            import soundfile as sf
            path=Path(request["output_path"])
            path.parent.mkdir(parents=True,exist_ok=True)
            fragments=[]
            for sr,audio in engine.run({
                "text":str(request["text"]),"text_lang":"all_zh",
                "ref_audio_path":str(HOME/"community-model/ref/ref.wav"),
                "prompt_text":(HOME/"community-model/ref/prompt.txt").read_text(encoding="utf-8").strip(),
                "prompt_lang":"all_zh","top_k":10,"top_p":1,"temperature":0.75,
                "text_split_method":"cut0","batch_size":1,"speed_factor":float(request.get("rate",1)),
                "split_bucket":False,"return_fragment":False,"seed":1234,
                "parallel_infer":True,"repetition_penalty":1.35,
            }):
                fragments.append(audio)
            if not fragments:raise RuntimeError("本地模型没有返回音频")
            audio=np.concatenate(fragments)
            if not np.isfinite(audio).all() or len(audio)<sr*0.15 or not np.any(audio):
                raise RuntimeError("本地模型返回空白或无效音频，请检查推理日志")
            sf.write(str(path),audio,sr)
            result={"ok":True,"provider":"tianyi-local","model":"tianyi-community-v2pro-cb5fed8d-1bd07ccc-v1",
                    "device":device,"sample_rate":sr,"duration_sec":round(len(audio)/sr,4),"pitch_applied":False}
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            result={"ok":False,"error":f"{type(exc).__name__}: {exc}"}
        print(MARKER+json.dumps(result,ensure_ascii=False),flush=True)


if __name__=="__main__":
    if "--check" in sys.argv:
        _, device = load_engine()
        print("TIANYI_ENGINE_READY:" + device, flush=True)
    else:
        main()
