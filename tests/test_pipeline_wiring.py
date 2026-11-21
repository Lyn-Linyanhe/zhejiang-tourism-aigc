"""画面 provider 的参数传导、AIGC 标识、图生视频时长下限。

这几处都是"接线"缺陷：模块各自都对，但参数在 registry 边界上被丢掉了，
或者某个字段被硬编码成竖屏。所以这里测的是接线，不是各模块的内部逻辑。
"""

import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

from backend.app.providers.media_ffmpeg import generate_tone

from backend.app.config import Settings
from backend.app.models import AssetRecord, Shot, Task, TaskInput
from backend.app.providers.image import build_image_payload
from backend.app.providers.llm import (
    NARRATION_CHARS_PER_SECOND,
    OpenAICompatibleLLMProvider,
    plan_narration_budget,
)
from backend.app.services.audio_sync import PerShotAudioBuilder
from backend.app.services.orchestrator import Orchestrator
from backend.app.storage import Storage
from backend.app.providers.media_ffmpeg import (
    AIGC_LABEL_TEXT,
    aigc_label_filter,
    find_cjk_font,
)
from backend.app.providers.registry import invoke_create_or_find
from backend.app.providers.tts import (
    EDGE_VOICE_CHOICES,
    clause_pause_sec,
    edge_voice_name,
    split_clauses,
)
from backend.app.providers.video import (
    MAX_DURATION,
    MIN_DURATION,
    CloudVideoProvider,
    _is_error_envelope,
    clamp_duration,
)
from backend.app.services.consistency import output_spec
from backend.app.services.renderer import VideoRenderer


class _FiveArgProvider:
    """老 provider 的冻结合同：create_or_find 只收 5 个位置参数。"""

    name = "legacy"

    def __init__(self):
        self.received = None

    def create_or_find(self, task_id, shot, output_dir, theme_color, accent):
        self.received = (task_id, shot, theme_color, accent)
        return {"local_path": "unused"}


class _SixArgProvider:
    """P2 画面适配器：多收一个 style_ctx。"""

    name = "cloud"

    def __init__(self):
        self.received = None

    def create_or_find(self, task_id, shot, output_dir, theme_color, accent, style_ctx=None):
        self.received = style_ctx
        return {"local_path": "unused"}


class OutputSpecTests(unittest.TestCase):
    def test_maps_each_output_resolution_to_provider_terms(self):
        self.assertEqual(
            output_spec("720x1280"), {"ratio": "9:16", "size": "1440x2560"}
        )
        self.assertEqual(output_spec("1920x1080")["ratio"], "16:9")
        self.assertEqual(output_spec("1080x1080")["ratio"], "1:1")
        self.assertEqual(output_spec("1920x1080")["size"], "2560x1440")

    def test_never_asserts_a_video_resolution(self):
        # AI 视频分辨率按账号计价，网关会拒绝未配价的口径（实测 720P 被拒、768P 通过）。
        # 所以这里只认运维配置的 VIDEO_RESOLUTION，不能从成片尺寸推断。
        for resolution in ("720x1280", "1080x1920", "1920x1080", "1080x1080"):
            self.assertNotIn("resolution", output_spec(resolution))

    def test_unknown_resolution_falls_back_to_configured_size_only(self):
        self.assertEqual(output_spec("2048x1152", fallback_size="2K"), {"size": "2K"})
        self.assertEqual(output_spec("2048x1152"), {})


class StyleContextPropagationTests(unittest.TestCase):
    def test_style_ctx_reaches_providers_that_declare_it(self):
        provider = _SixArgProvider()
        ctx = {"style": "诗意纪实", "resolution": "720p", "ratio": "9:16", "size": "1440x2560"}
        invoke_create_or_find(provider, "task", {"id": "shot_1"}, Path("."), ctx)
        self.assertEqual(provider.received["resolution"], "720p")
        self.assertEqual(provider.received["ratio"], "9:16")
        self.assertEqual(provider.received["style"], "诗意纪实")

    def test_legacy_providers_still_get_five_positional_arguments(self):
        provider = _FiveArgProvider()
        invoke_create_or_find(provider, "task", {"id": "shot_1"}, Path("."), {"color": "#111"})
        self.assertEqual(provider.received, ("task", {"id": "shot_1"}, "#111", "#d6ad60"))


class VoiceCatalogTests(unittest.TestCase):
    def test_named_voices_map_to_edge_short_names(self):
        self.assertEqual(edge_voice_name("晓伊·活泼女声"), "zh-CN-XiaoyiNeural")
        self.assertEqual(edge_voice_name("云健·沉稳男声"), "zh-CN-YunjianNeural")

    def test_legacy_voice_values_still_resolve(self):
        # 历史任务的 voice 字段是"女声"/"男声"，不能因为改了音色表就跑不了
        self.assertEqual(edge_voice_name("女声"), "zh-CN-XiaoxiaoNeural")
        self.assertEqual(edge_voice_name("男声"), "zh-CN-YunxiNeural")

    def test_unknown_voice_passes_through_as_a_short_name(self):
        self.assertEqual(edge_voice_name("zh-CN-YunyangNeural"), "zh-CN-YunyangNeural")

    def test_every_offered_choice_resolves_to_a_real_edge_voice(self):
        # 方言音色多一段（zh-CN-liaoning-XiaobeiNeural），所以不能只允许一个 \w+
        for name in EDGE_VOICE_CHOICES:
            self.assertRegex(edge_voice_name(name), r"^zh-CN-[\w-]+Neural$")


class _RateAwareTtsBuilder(PerShotAudioBuilder):
    """假 TTS：产出长度与语速成反比，用来观察超时长时的提速行为，不打网络。"""

    def __init__(self, settings) -> None:
        super().__init__(settings, runtime=None)
        self.rates: list[float] = []

    def _synthesize_cached(self, text, voice, rate, force_provider="", pitch=0.0):
        self.rates.append(round(float(rate), 4))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_dir / f"fake-{round(float(rate), 4)}-{len(self.rates)}.wav"
        generate_tone(path, 1.0 / max(float(rate), 0.2), 440, 0.1)
        return path, {"provider": "stub", "model": "stub", "cache_hit": False}


class NarrationRateFitTests(unittest.TestCase):
    def _settings(self, root: Path):
        settings = SimpleNamespace(
            data_dir=root / "runtime",
            media_dir=root / "media",
            tts_base_url="",
            tts_api_key="",
            tts_model="",
        )
        return settings

    def _shots(self, count: int = 2) -> list[Shot]:
        return [
            Shot(
                id=f"shot_{index}",
                order=index,
                duration_sec=1.0,
                title="t",
                visual_prompt="v",
                narration="断桥相遇传说千年",
                subtitle="断桥相遇传说千年",
            )
            for index in range(1, count + 1)
        ]

    def test_overrunning_narration_is_sped_up_to_fit(self):
        # 中文 TTS 实测 3.4~3.9 字/秒，字数预算压不住这 12% 的波动；
        # 超出的部分会被渲染器裁掉，最后一句听着就是断的。
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            builder = _RateAwareTtsBuilder(self._settings(root))
            shots = self._shots(2)
            result = builder.build(
                "task_fit", shots, voice="晓伊·活泼女声", rate=1.0, target_duration=2.0
            )
            self.assertGreater(result.event["rate_scale"], 1.0)
            self.assertEqual(len(builder.rates), 4)  # 两镜 × 两轮
            self.assertGreater(builder.rates[-1], builder.rates[0])
            self.assertLessEqual(result.total_duration, 2.0 + 0.05)

    def test_narration_inside_the_budget_is_not_sped_up(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            builder = _RateAwareTtsBuilder(self._settings(root))
            result = builder.build(
                "task_ok", self._shots(2), voice="晓伊·活泼女声", rate=1.0, target_duration=5.0
            )
            self.assertEqual(result.event["rate_scale"], 1.0)
            self.assertEqual(len(builder.rates), 2)  # 只跑一轮

    def test_uploaded_audio_is_never_sped_up(self):
        # 上传的是用户给的原始录音，提速改不了它，重跑一轮纯属浪费。
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = self._settings(root)
            uploaded = root / "up.wav"
            generate_tone(uploaded, 1.4, 440, 0.1)
            builder = _RateAwareTtsBuilder(settings)
            shots = self._shots(2)
            for shot in shots:
                shot.audio_local_path = str(uploaded)
            result = builder.build(
                "task_up", shots, voice="晓伊·活泼女声", rate=1.0, target_duration=2.0
            )
            self.assertEqual(result.event["rate_scale"], 1.0)
            self.assertEqual(builder.rates, [])


class TtsCacheKeyTests(unittest.TestCase):
    def test_pitch_is_part_of_the_cache_key(self):
        # 只改音高时若缓存键不变，会命中旧音频，用户看到"改了没效果"。
        base = PerShotAudioBuilder._cache_key(
            "edge-tts", "zh-CN-XiaoyiNeural", "晓伊·活泼女声", 1.0, "断桥相遇", 0.0
        )
        raised = PerShotAudioBuilder._cache_key(
            "edge-tts", "zh-CN-XiaoyiNeural", "晓伊·活泼女声", 1.0, "断桥相遇", 30.0
        )
        self.assertNotEqual(base, raised)


class ClauseSplitTests(unittest.TestCase):
    def test_clauses_keep_their_punctuation(self):
        # 标点要跟着前一段走：edge-tts 靠句末标点决定降调，去掉会像被硬切断
        self.assertEqual(
            split_clauses("断桥相遇，传说千年。"),
            ["断桥相遇，", "传说千年。"],
        )

    def test_clause_pause_is_longer_at_a_period_than_a_comma(self):
        self.assertGreater(clause_pause_sec("传说千年。"), clause_pause_sec("断桥相遇，"))

    def test_text_without_punctuation_is_a_single_clause(self):
        self.assertEqual(split_clauses("断桥相遇传说千年"), ["断桥相遇传说千年"])

    def test_punctuation_only_segments_are_dropped(self):
        self.assertEqual(split_clauses("，，。甲，"), ["甲，"])


class NarrationBudgetTests(unittest.TestCase):
    def test_budget_plus_inter_shot_silence_fits_the_container(self):
        # 这是"结尾被裁掉"的根因检查：按每镜上限写满，加上镜间静音，
        # 也必须落在用户选的秒数之内。
        for duration in (15, 30, 60):
            for shot_count in (4, 6, 8):
                with self.subTest(duration=duration, shot_count=shot_count):
                    budget = plan_narration_budget(duration, shot_count)
                    speech = budget["total_chars"] / NARRATION_CHARS_PER_SECOND
                    self.assertLessEqual(
                        speech + budget["silence_total"], float(duration) + 0.05
                    )

    def test_per_shot_cap_never_multiplies_past_the_total_budget(self):
        for duration in (15, 30, 60):
            for shot_count in (4, 6, 8):
                with self.subTest(duration=duration, shot_count=shot_count):
                    budget = plan_narration_budget(duration, shot_count)
                    self.assertLessEqual(
                        budget["per_shot_chars"] * shot_count, budget["total_chars"]
                    )
                    self.assertLessEqual(
                        budget["per_shot_floor"], budget["per_shot_chars"]
                    )


def _video_duration(path: Path) -> float:
    done = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "csv=p=0", str(path),
        ],
        capture_output=True, text=True, check=False,
    )
    return float(done.stdout.strip() or 0)


def _reply(chars_per_shot: int, shot_count: int = 4) -> dict:
    text = "字" * chars_per_shot
    return {
        "title": "标题",
        "summary": "摘要",
        "cta": "号召",
        "tags": ["杭州"],
        "narration": text,
        "shots": [
            {
                "id": f"shot_{index}",
                "order": index,
                "duration_sec": 3.75,
                "title": f"镜头{index}",
                "visual_prompt": "西湖断桥晨雾",
                "narration": text,
                "subtitle": text,
                "transition": "fade",
            }
            for index in range(1, shot_count + 1)
        ],
    }


class _ScriptedChatProvider(OpenAICompatibleLLMProvider):
    """把 _chat 换成准备好的回复，用来测字数兜底逻辑，不打网络。"""

    def __init__(self, replies: list[dict]) -> None:
        super().__init__("http://llm.invalid", "test-key", "test-model")
        self.replies = list(replies)
        self.calls = 0

    def _chat(self, messages):  # type: ignore[override]
        self.calls += 1
        return self.replies.pop(0)


class NarrationEnforcementTests(unittest.TestCase):
    def _generate(self, replies: list[dict]):
        provider = _ScriptedChatProvider(replies)
        result = provider.generate("杭州西湖", {"duration": 15, "shot_count": 4})
        return provider, result

    def test_short_script_is_rewritten_into_the_target_band(self):
        # 实测模型会因为"七言诗"节奏只写 7 字/镜，成片后半段只剩静止画面。
        provider, result = self._generate([_reply(7), _reply(12)])
        self.assertEqual(provider.calls, 2)
        self.assertEqual(result.event["narration_rewrite_attempts"], 1)
        self.assertEqual(result.event["narration_chars"], 48)
        self.assertEqual(result.data["shots"][0]["narration"], "字" * 12)

    def test_rewrite_retries_once_more_when_still_out_of_band(self):
        # 一轮改写不总能落进区间（实测 29 字只改到 42 字），所以给第二轮。
        provider, result = self._generate([_reply(7), _reply(9), _reply(12)])
        self.assertEqual(provider.calls, 3)
        self.assertEqual(result.event["narration_rewrite_attempts"], 2)
        self.assertEqual(result.event["narration_chars"], 48)

    def test_overlong_script_is_also_rewritten(self):
        # 另一个方向：写超了会在渲染时把最后一句的结尾裁掉。
        provider, result = self._generate([_reply(20), _reply(12)])
        self.assertEqual(provider.calls, 2)
        self.assertEqual(result.event["narration_chars"], 48)

    def test_script_inside_the_band_is_not_rewritten(self):
        provider, result = self._generate([_reply(12)])
        self.assertEqual(provider.calls, 1)
        self.assertEqual(result.event["narration_rewrite_attempts"], 0)


class AssetReuseTests(unittest.TestCase):
    def _orchestrator(self, root: Path) -> Orchestrator:
        settings = Settings(data_dir=root)
        settings.ensure_dirs()
        return Orchestrator(settings, Storage(settings.db_path))

    def test_reused_asset_keeps_its_media_metadata(self):
        # 复用一个已经落盘的 mp4 时不能再调 provider，但 media_type / model / seed
        # 必须原样留在 AssetRecord 里，否则 manifest 和作品档案读不到素材来源。
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            orchestrator = self._orchestrator(root)
            clip = orchestrator.settings.media_dir / "task_reuse" / "assets" / "shot_1.mp4"
            clip.parent.mkdir(parents=True, exist_ok=True)
            clip.write_bytes(b"\x00" * 64)

            task = Task(
                id="task_reuse",
                input=TaskInput.from_dict(
                    {
                        "city": "杭州",
                        "theme": "西湖春游",
                        "duration": 15,
                        "shot_count": 4,
                        "asset_source": "ai",
                    }
                ),
            )
            task.shots = [
                Shot(
                    id="shot_1",
                    order=1,
                    duration_sec=3.0,
                    title="断桥",
                    visual_prompt="v",
                    narration="n",
                    subtitle="s",
                )
            ]
            task.assets = [
                AssetRecord(
                    id="cloud-i2v_shot_1",
                    task_id="task_reuse",
                    shot_id="shot_1",
                    name="断桥",
                    url="/media/task_reuse/assets/shot_1.mp4",
                    source="cloud-i2v",
                    license="l",
                    author="minimax-h3",
                    usage_scope="u",
                    is_ai_generated=True,
                    local_path=str(clip),
                    media_type="video",
                    model="minimax-h3",
                    seed=12345,
                    prompt="p",
                    first_frame_path="f",
                    cost=0.0,
                )
            ]

            orchestrator._assets(task)

            self.assertEqual(len(task.assets), 1)
            reused = task.assets[0]
            self.assertEqual(reused.local_path, str(clip))
            self.assertEqual(reused.media_type, "video")
            self.assertEqual(reused.model, "minimax-h3")
            self.assertEqual(reused.seed, 12345)
            # 复用后镜头上的 media_type 也要在，渲染器据此判断是不是视频素材。
            self.assertEqual(task.shots[0].asset_media_type, "video")


class GatewayErrorEnvelopeTests(unittest.TestCase):
    def test_error_envelope_is_recognised_as_a_query_failure(self):
        # 网关出错返回 {"error":…,"request_id":…,"type":…}，没有 status。
        # 不认出来的话轮询会去读 status，报出 'unrecognized status ""'，
        # 把真实原因（限流/额度/参数）整个吞掉。
        self.assertTrue(
            _is_error_envelope(
                {
                    "error": {"message": "API 密钥达到最大额度", "code": "api_key_quota_exceeded"},
                    "request_id": "req-1",
                    "type": "error",
                }
            )
        )

    def test_a_failed_task_is_not_mistaken_for_a_query_failure(self):
        # 有 status 的失败任务要走"任务失败"分支，不能被当成查询失败去重试。
        self.assertFalse(
            _is_error_envelope({"status": "failed", "error": {"message": "content policy"}})
        )

    def test_a_normal_running_task_is_not_an_error(self):
        self.assertFalse(_is_error_envelope({"status": "running"}))


class _SlowRegistry:
    """假注册表：每次生成睡一会，用来观察并发是否真的发生。"""

    def __init__(self, delay: float = 0.25, fail_shots: tuple[str, ...] = ()) -> None:
        self.delay = delay
        self.fail_shots = set(fail_shots)
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def asset_chain_for(self, source: str) -> list[str]:
        return ["fake"]

    def generate_asset(self, name, task_id, shot, output_dir, style_ctx):
        with self._lock:
            self.calls.append(shot["id"])
        time.sleep(self.delay)
        if shot["id"] in self.fail_shots:
            raise RuntimeError(f"boom {shot['id']}")
        path = Path(output_dir) / f"{shot['id']}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG\r\n\x1a\n")
        return {
            "id": f"fake_{shot['id']}",
            "name": shot["id"],
            "url": f"/media/x/{shot['id']}.png",
            "local_path": str(path),
            "source": "fake",
            "license": "l",
            "author": "a",
            "usage_scope": "u",
            "is_ai_generated": True,
            "media_type": "image",
            "model": "fake-model",
            "seed": 7,
            "prompt": "p",
            "first_frame_path": "",
            "cost": 0.0,
            "event": {},
        }


class CueTimingTests(unittest.TestCase):
    def _task_with_cues(self) -> Task:
        task = Task(
            id="task_cues",
            input=TaskInput.from_dict(
                {"city": "杭州", "theme": "西湖", "duration": 15, "shot_count": 4}
            ),
        )
        task.shots = [
            Shot(
                id=f"shot_{index}",
                order=index,
                duration_sec=1.0,
                title="t",
                visual_prompt="v",
                narration="n",
                subtitle="s",
            )
            for index in range(1, 4)
        ]
        # 每镜两句：句中停顿把同一镜的两条 cue 分开
        task.audio = {
            "cues": [
                {"shot_id": "shot_1", "start": 0.0, "end": 1.0, "text": "甲"},
                {"shot_id": "shot_1", "start": 1.2, "end": 2.2, "text": "乙"},
                {"shot_id": "shot_2", "start": 2.5, "end": 3.5, "text": "丙"},
                {"shot_id": "shot_3", "start": 3.8, "end": 5.0, "text": "丁"},
            ]
        }
        return task

    def test_shot_durations_include_the_gap_up_to_the_next_shot(self):
        # 镜头时长必须含镜后停顿。只算本镜末句终点会漏掉停顿，各镜之和短于
        # 人声轨，画面每过一个镜头边界超前，八镜累计超过一秒。
        task = self._task_with_cues()
        Orchestrator._sync_shots_to_audio(task)
        self.assertAlmostEqual(task.shots[0].duration_sec, 2.5, places=3)  # 0.0 -> 下一镜起点 2.5
        self.assertAlmostEqual(task.shots[1].duration_sec, 1.3, places=3)  # 2.5 -> 3.8
        self.assertAlmostEqual(task.shots[2].duration_sec, 1.2, places=3)  # 3.8 -> 末句终点 5.0
        self.assertAlmostEqual(
            sum(shot.duration_sec for shot in task.shots),
            max(cue["end"] for cue in task.audio["cues"]),
            places=3,
        )


class ParallelAssetTests(unittest.TestCase):
    def _task(self) -> Task:
        task = Task(
            id="task_parallel",
            input=TaskInput.from_dict(
                {
                    "city": "杭州",
                    "theme": "西湖春游",
                    "duration": 15,
                    "shot_count": 4,
                    "asset_source": "offline",
                }
            ),
        )
        task.shots = [
            Shot(
                id=f"shot_{index}",
                order=index,
                duration_sec=3.0,
                title=f"镜头{index}",
                visual_prompt="西湖",
                narration="旁白",
                subtitle="字幕",
            )
            for index in range(1, 5)
        ]
        return task

    def _orchestrator(self, root: Path, registry) -> Orchestrator:
        settings = Settings(data_dir=root)
        settings.ensure_dirs()
        orchestrator = Orchestrator(settings, Storage(settings.db_path), registry=registry)
        return orchestrator

    def test_reference_is_shared_only_by_group_and_survives_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = _SlowRegistry(delay=0)
            registry.asset_chain_for = lambda source: ["ai-video"]
            app = self._orchestrator(Path(directory), registry)
            task = self._task()
            key = "a" * 64
            folder = app.settings.media_dir / task.id / "references"
            folder.mkdir(parents=True)
            ref = folder / (key + ".jpg")
            ref.touch()
            task.shots[0].subject_group = task.shots[1].subject_group = "塔"
            task.shots[0].subject_reference = key
            frames = {}
            def first_frame(task, shot, payload, *args, **kwargs):
                frames[shot.id] = payload.get("first_frame_path", "independent")
                return frames[shot.id]
            with patch.object(app, "_ensure_first_frame", side_effect=first_frame), patch("backend.app.services.orchestrator.review_group", return_value={"file":"sheet.jpg"}):
                app._assets(task)
                app._assets(task)
            self.assertEqual(frames["shot_1"], str(ref))
            self.assertEqual(frames["shot_2"], str(ref))
            self.assertEqual(frames["shot_3"], "independent")
            self.assertEqual(len(registry.calls), 4)

    def test_shots_are_generated_concurrently_not_one_by_one(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = _SlowRegistry(delay=0.3)
            orchestrator = self._orchestrator(Path(directory), registry)
            task = self._task()

            started = time.monotonic()
            orchestrator._assets(task)
            elapsed = time.monotonic() - started

            # 串行至少要 4 × 0.3 = 1.2 秒；并发多少都要明显快过它
            self.assertLess(elapsed, 0.9)
            self.assertEqual(len(registry.calls), 4)

    def test_committed_assets_keep_the_shot_order(self):
        # 提交顺序必须是分镜顺序，不能是完成顺序（快的先回来很正常）
        with tempfile.TemporaryDirectory() as directory:
            registry = _SlowRegistry(delay=0.05)
            orchestrator = self._orchestrator(Path(directory), registry)
            task = self._task()
            orchestrator._assets(task)
            self.assertEqual(
                [asset.shot_id for asset in task.assets],
                ["shot_1", "shot_2", "shot_3", "shot_4"],
            )
            self.assertEqual(task.assets[0].media_type, "image")
            self.assertEqual(task.assets[0].model, "fake-model")

    def test_one_failed_shot_does_not_discard_the_others(self):
        # 已成功的镜头要先提交：素材已经落盘，重试时能直接复用，
        # 不该把付过费的那几镜重跑一遍。
        with tempfile.TemporaryDirectory() as directory:
            registry = _SlowRegistry(delay=0.05, fail_shots=("shot_2",))
            orchestrator = self._orchestrator(Path(directory), registry)
            task = self._task()
            with self.assertRaises(RuntimeError):
                orchestrator._assets(task)
            self.assertEqual([asset.shot_id for asset in task.assets], ["shot_1", "shot_3", "shot_4"])


class TextToVideoTests(unittest.TestCase):
    """文生视频：同一个模型，只是不带首帧，但必须显式给 ratio。"""

    def _provider(self, require_first_frame: bool):
        provider = CloudVideoProvider.__new__(CloudVideoProvider)
        provider.base_url = "https://gw.invalid/minimax/v2"
        provider.api_key = "k"
        provider.model = "minimax-h3"
        provider.resolution = "768p"
        provider.ratio = "9:16"
        provider.api_style = "tokendance"
        provider.poll_interval = 1
        provider.timeout = 30
        provider.require_first_frame = require_first_frame
        return provider

    def _capture_body(self, require_first_frame: bool, frame: Path | None) -> dict:
        """走 generate() 这个真实入口——首帧守卫在那一层，绕过它就测不到。"""
        import backend.app.providers.video as video_module

        provider = self._provider(require_first_frame)
        captured: dict = {}

        def fake_request(url, method, body=None):
            if method == "POST":
                captured.update(body or {})
                return {"task_id": "tsk-test"}
            return {"task": {"status": "succeeded", "content": {"url": "https://cdn.invalid/v.mp4"}}}

        provider._tokendance_request = fake_request
        shot = {"id": "shot_1", "order": 1, "visual_prompt": "西湖断桥", "duration_sec": 5}
        if frame is not None:
            shot["first_frame_path"] = str(frame)
        with tempfile.TemporaryDirectory() as directory:
            original = video_module.download_video
            video_module.download_video = lambda url, dest: dest.write_bytes(b"v")
            try:
                provider.generate(shot, Path(directory), {"task_id": "t", "duration_sec": 5})
            finally:
                video_module.download_video = original
        return captured

    def _frame(self, root: Path) -> Path:
        frame = root / "frame.png"
        frame.write_bytes(b"\x89PNG\r\n\x1a\n")
        return frame

    def test_text_to_video_sends_ratio_and_no_image_item(self):
        # 不带 ratio 网关会报 "ratio is required for text-to-video"；
        # 带了首帧就退回成图生视频，画面运动只剩推镜。
        with tempfile.TemporaryDirectory() as directory:
            body = self._capture_body(False, None)
        self.assertEqual(body.get("ratio"), "9:16")
        kinds = [item["type"] for item in body["content"]]
        self.assertEqual(kinds, ["text"])

    def test_image_to_video_keeps_the_first_frame_and_no_ratio(self):
        with tempfile.TemporaryDirectory() as directory:
            body = self._capture_body(True, self._frame(Path(directory)))
        kinds = [item["type"] for item in body["content"]]
        self.assertEqual(kinds, ["text", "image_url"])
        self.assertNotIn("ratio", body)

    def test_text_to_video_ignores_a_stray_first_frame(self):
        # require_first_frame=False 时给了首帧也不能用，否则又变回图生视频。
        with tempfile.TemporaryDirectory() as directory:
            body = self._capture_body(False, self._frame(Path(directory)))
        kinds = [item["type"] for item in body["content"]]
        self.assertEqual(kinds, ["text"])


class ImagePayloadTests(unittest.TestCase):
    def test_tokendance_protocol_folds_the_negative_prompt_into_the_text(self):
        # Seedream 协议没有 negative_prompt 字段，不折进 prompt 就等于没传，
        # 默认负向词里的"文字，水印，logo"会整条失效。
        payload = build_image_payload(
            "西湖薄雾",
            model="seedream-5.0-pro",
            size="1440x2560",
            seed=42,
            negative="文字，水印，logo",
            api_style="tokendance",
        )
        self.assertIn("文字，水印，logo", payload["prompt"])
        self.assertIn("西湖薄雾", payload["prompt"])

    def test_openai_protocol_keeps_the_negative_prompt_in_its_own_field(self):
        payload = build_image_payload(
            "西湖薄雾",
            model="local-test",
            size="1024x1536",
            seed=42,
            negative="文字，水印",
            api_style="openai-compatible",
        )
        self.assertEqual(payload["negative_prompt"], "文字，水印")
        self.assertNotIn("文字", payload["prompt"])


class AigcLabelTests(unittest.TestCase):
    def test_filter_escapes_font_path_colon_and_scales_with_height(self):
        small = aigc_label_filter(AIGC_LABEL_TEXT, 720, 1280, Path(r"C:\Windows\Fonts\msyh.ttc"))
        large = aigc_label_filter(AIGC_LABEL_TEXT, 1080, 1920, Path(r"C:\Windows\Fonts\msyh.ttc"))
        self.assertIn(r"fontfile='C\:/Windows/Fonts/msyh.ttc'", small)
        self.assertIn("expansion=none", small)
        self.assertLess(
            int(small.split("fontsize=")[1].split(":")[0]),
            int(large.split("fontsize=")[1].split(":")[0]),
        )

    def test_filter_escapes_text_metacharacters(self):
        rendered = aigc_label_filter("AI 100%: 标注", 720, 1280, Path("C:/f.ttf"))
        self.assertIn(r"100\%", rendered)
        self.assertIn(r"\:", rendered)


class VideoDurationClampTests(unittest.TestCase):
    def test_short_shots_never_request_below_the_provider_minimum(self):
        # 逐镜配音反推出来的镜头时长经常只有 2 秒出头，那种请求会被网关拒掉。
        self.assertEqual(clamp_duration(2.1), MIN_DURATION)
        self.assertEqual(clamp_duration(0), MIN_DURATION)

    def test_long_shots_are_capped(self):
        self.assertEqual(clamp_duration(30), MAX_DURATION)
        self.assertEqual(clamp_duration("bogus"), MIN_DURATION)


class RenderPaddingTests(unittest.TestCase):
    def test_short_footage_is_padded_up_to_the_requested_duration(self):
        # tpad 的 stop_duration 曾写死 1 秒：镜头合计比请求秒数短超过 1 秒时补不满，
        # 成片会短于用户选的秒数（实测请求 15.0 秒出 14.48 秒）。
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "scene.ppm"
            asset.write_bytes(
                b"P6\n2 2\n255\n"
                + bytes((20, 90, 120, 20, 90, 120, 230, 180, 80, 230, 180, 80))
            )
            render = VideoRenderer(root / "media").render(
                "task_pad",
                [
                    {
                        "id": "shot_1",
                        "duration_sec": 2,
                        "subtitle": "字幕",
                        "asset_local_path": str(asset),
                    }
                ],
                {"requested_duration": 6.0},
                False,
                True,
                "720x1280",
                25,
                1.0,
                0.0,
            )
            self.assertAlmostEqual(render["total_duration"], 6.0, places=2)
            self.assertAlmostEqual(_video_duration(Path(render["video_local_path"])), 6.0, places=2)


class RenderOverlayTests(unittest.TestCase):
    def _render(self, root: Path, resolution: str, include_ai_label: bool) -> dict:
        asset = root / "scene.ppm"
        asset.write_bytes(
            b"P6\n2 2\n255\n"
            + bytes((20, 90, 120, 20, 90, 120, 230, 180, 80, 230, 180, 80))
        )
        return VideoRenderer(root / "media").render(
            "task_label",
            [
                {
                    "id": "shot_1",
                    "duration_sec": 1,
                    "subtitle": "测试字幕",
                    "asset_local_path": str(asset),
                }
            ],
            {},
            include_ai_label,
            True,
            resolution,
            25,
            1.0,
            0.0,
        )

    def test_aspect_ratio_is_derived_from_the_requested_resolution(self):
        with tempfile.TemporaryDirectory() as directory:
            render = self._render(Path(directory), "720x1280", True)
            # 约分到最简，不能是 45:80 这种"能被 16 整除但不是最简"的结果。
            self.assertEqual(render["aspect_ratio"], "9:16")

    def test_label_is_burned_and_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            render = self._render(Path(directory), "720x1280", True)
            # 缺中文字体的机器上不烧，但必须在 manifest 里如实记录。
            self.assertEqual(render["has_burned_aigc_label"], find_cjk_font() is not None)

    def test_label_is_absent_when_user_disables_it(self):
        with tempfile.TemporaryDirectory() as directory:
            render = self._render(Path(directory), "720x1280", False)
            self.assertFalse(render["has_burned_aigc_label"])
            self.assertEqual(render["aigc_label"], "")


if __name__ == "__main__":
    unittest.main()
