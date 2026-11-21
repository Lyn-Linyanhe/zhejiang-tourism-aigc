"""Install pinned community Tianyi inference assets in a separate local runtime."""
from __future__ import annotations
import concurrent.futures
import hashlib
import json
import shutil
import time
import urllib.request
import zipfile
from pathlib import Path
from download_voice_assets import ranged_download

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / "runtime" / "tianyi"
ENGINE_SHA = "2ff2cf5ba19e10f4f3fab7df51642c87080dd4ae"
MODEL_SHA = "61c41aedd2edb47874f3a8d1f9925c2e17ed797d"
BASE_SHA = "8f8857563c040d542de108a2c18b0425abd6c9c5"


def download(url: str, target: Path, expected: str = "") -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and (not expected or digest(target) == expected):
        print("EXISTS", target.name, flush=True)
        return
    if target.suffix in {".ckpt", ".pth", ".bin"} or target.name == "G2PWModel_1.1.zip":
        ranged_download(url,target,expected,workers=6)
        return
    partial = target.with_suffix(target.suffix + ".part")
    for attempt in range(3):
        try:
            headers = {"User-Agent":"VoiceStudio/1.0", "Accept":"application/octet-stream"}
            if "/contents/" in url:
                headers["Accept"] = "application/vnd.github.raw+json"
            with urllib.request.urlopen(urllib.request.Request(url,headers=headers), timeout=60) as response, partial.open("wb") as output:
                total = 0
                while block := response.read(1024*1024):
                    output.write(block); total += len(block)
                    if total % (64*1024*1024) == 0: print("PROGRESS", target.name, total//1024//1024, "MiB",flush=True)
            if expected and digest(partial) != expected:
                raise ValueError("SHA-256 mismatch: " + target.name)
            partial.replace(target)
            print("DOWNLOADED", target.name, target.stat().st_size, flush=True)
            return
        except Exception as exc:
            print("RETRY",target.name,str(exc),flush=True)
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()


def main() -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    archive = HOME / "engine.zip"
    engine = HOME / "engine"
    if not engine.exists():
        download(f"https://codeload.github.com/RVC-Boss/GPT-SoVITS/zip/{ENGINE_SHA}", archive)
        with zipfile.ZipFile(archive) as bundle:
            base = HOME.resolve()
            if any(not (base / name).resolve().is_relative_to(base) for name in bundle.namelist()):
                raise ValueError("Unsafe archive path")
            bundle.extractall(HOME)
        shutil.copytree(HOME / f"GPT-SoVITS-{ENGINE_SHA}", engine, dirs_exist_ok=True)
    jobs = []
    for filename, (asset_id,sha) in {
        "tianyi2-e15.ckpt":(583839023,"cb5fed8d387c6964c5acc778ee09860a8daa7bb9f03f93d8143ca127728626b0"),
        "tianyi2_e8_s128.pth":(583839176,"1bd07ccc9381bab4ea6dd91a0c11d9cf7091021e81e58a3082b55f693b3b5aca"),
    }.items():
        jobs.append((f"https://api.github.com/repos/Kevin14827/LuoTianyi-TTS/releases/assets/{asset_id}", HOME/"weights"/filename, sha))
    community_zip=HOME/"community-model.zip"
    download(f"https://codeload.github.com/Kevin14827/LuoTianyi-TTS/zip/{MODEL_SHA}",community_zip)
    with zipfile.ZipFile(community_zip) as bundle:
        prefix=f"LuoTianyi-TTS-{MODEL_SHA}/"
        for name in ("ref/ref.wav", "ref/prompt.txt", "README.md", "LICENSE"):
            dest=HOME/"community-model"/name;dest.parent.mkdir(parents=True,exist_ok=True)
            dest.write_bytes(bundle.read(prefix+name))
    for name in ("chinese-hubert-base/config.json", "chinese-hubert-base/preprocessor_config.json",
                 "chinese-hubert-base/pytorch_model.bin", "chinese-roberta-wwm-ext-large/config.json",
                 "chinese-roberta-wwm-ext-large/pytorch_model.bin", "chinese-roberta-wwm-ext-large/tokenizer.json",
                 "sv/pretrained_eres2netv2w24s4ep4.ckpt"):
        jobs.append((f"https://huggingface.co/lj1995/GPT-SoVITS/resolve/{BASE_SHA}/{name}", engine/"GPT_SoVITS/pretrained_models"/name, ""))
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(download,*job) for job in jobs]
        for future in concurrent.futures.as_completed(futures):
            future.result()
    g2pw_zip=HOME/"G2PWModel_1.1.zip"
    download("https://www.modelscope.cn/models/kamiorinn/g2pw/resolve/master/G2PWModel_1.1.zip",g2pw_zip)
    with zipfile.ZipFile(g2pw_zip) as bundle:
        for item in bundle.infolist():
            if item.is_dir():continue
            relative=Path(*Path(item.filename).parts[1:])
            base=(engine/"GPT_SoVITS/text/G2PWModel").resolve();dest=(base/relative).resolve()
            if not dest.is_relative_to(base):raise ValueError("Unsafe G2PW archive")
            dest.parent.mkdir(parents=True,exist_ok=True)
            with bundle.open(item) as source,dest.open("wb") as output:shutil.copyfileobj(source,output)
    manifest = {"engine_revision":ENGINE_SHA, "community_revision":MODEL_SHA,
                "base_revision":BASE_SHA, "source":"https://github.com/Kevin14827/LuoTianyi-TTS",
                "official_xstudio":False, "usage":"Community research model; no official commercial rights established.",
                "files":[{"path":str(path.relative_to(HOME)), "sha256":digest(path), "url":url} for url,path,_ in jobs]}
    for category, name in (("corpora", "cmudict"), ("taggers", "averaged_perceptron_tagger"),
                           ("taggers", "averaged_perceptron_tagger_eng")):
        url = f"https://api.github.com/repos/nltk/nltk_data/contents/packages/{category}/{name}.zip?ref=gh-pages"
        archive = HOME/"nltk_data"/category/(name+".zip")
        download(url, archive)
        with zipfile.ZipFile(archive) as bundle:
            base = archive.parent.resolve()
            if any(not (base/member).resolve().is_relative_to(base) for member in bundle.namelist()):
                raise ValueError("Unsafe NLTK archive")
            bundle.extractall(base)
        manifest["files"].append({"path":str(archive.relative_to(HOME)), "sha256":digest(archive), "url":url})
    for path, url in [
        (HOME/"engine.zip", f"https://codeload.github.com/RVC-Boss/GPT-SoVITS/zip/{ENGINE_SHA}"),
        (community_zip, f"https://codeload.github.com/Kevin14827/LuoTianyi-TTS/zip/{MODEL_SHA}"),
        (g2pw_zip, "https://www.modelscope.cn/models/kamiorinn/g2pw/resolve/master/G2PWModel_1.1.zip"),
        (HOME/"community-model/ref/ref.wav", f"https://github.com/Kevin14827/LuoTianyi-TTS/blob/{MODEL_SHA}/ref/ref.wav"),
        (HOME/"community-model/ref/prompt.txt", f"https://github.com/Kevin14827/LuoTianyi-TTS/blob/{MODEL_SHA}/ref/prompt.txt"),
    ]:
        if path.is_file():
            manifest["files"].append({"path":str(path.relative_to(HOME)), "sha256":digest(path), "url":url})
    (HOME/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    print("ASSETS_READY", HOME, flush=True)


if __name__ == "__main__":
    main()
