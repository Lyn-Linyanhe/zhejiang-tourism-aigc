from pathlib import Path
root=Path(r"C:\Users\ev041\Desktop\人工智能\zhejiang-tourism-aigc-merged")
p=root/'backend/app/services/orchestrator.py'; s=p.read_text(encoding='utf-8')
old='''                # 图生视频需要一张首帧，先备好；失败不打断这一镜
                if "ai-video" in candidates:
                    frame = self._ensure_first_frame(
                        task, shot, shot_payload, directory, knowledge, style_ctx
                    )
                    if frame:
                        shot_payload["first_frame_path"] = frame
'''
if old in s: s=s.replace(old,'')
p.write_text(s,encoding='utf-8')
p=root/'backend/app/providers/image.py'; s=p.read_text(encoding='utf-8')
old='''        self.size = resolved["size"]
        self.api_style = resolved["api_style"]
        if self.api_style == "tokendance" and not self.api_key:
            raise ImageConfigError("未配置 IMAGE_API_KEY。TokenDance 文生图需要 API key。")
        if not self.base_url:
            raise ImageConfigError(
                "未配置 IMAGE_BASE_URL。可以填 OpenAI 兼容云端，也可以填本地 ComfyUI 网关。"
            )
'''
new='''        self.size = resolved["size"]
        self.api_style = (
            "tokendance" if "tokendance.space" in self.base_url.lower() else "openai-compatible"
        ) if base_url else resolved["api_style"]
        if not self.base_url:
            raise ImageConfigError(
                "未配置 IMAGE_BASE_URL。可以填 OpenAI 兼容云端，也可以填本地 ComfyUI 网关。"
            )
        if self.api_style == "tokendance" and not self.api_key:
            raise ImageConfigError("未配置 IMAGE_API_KEY。TokenDance 文生图需要 API key。")
'''
if old not in s: raise SystemExit('image block missing')
s=s.replace(old,new); p.write_text(s,encoding='utf-8')
p=root/'backend/app/providers/video.py'; s=p.read_text(encoding='utf-8')
s=s.replace('import urllib.error\nimport urllib.request\n','import urllib.error\nimport urllib.parse\nimport urllib.request\n')
old='''        self.ratio = normalize_ratio(str(resolved["ratio"]))
        self.api_style = resolved["api_style"]
        self.poll_interval = max(1, int(resolved["poll_interval"]))
        self.timeout = max(30, int(resolved["timeout"]))
        if not self.api_key:
            raise VideoConfigError(
                "未配置 VIDEO_API_KEY。图生视频不会在缺少密钥时假装生成成功。"
            )
        if not self.base_url:
            raise VideoConfigError("未配置 VIDEO_BASE_URL。")
'''
new='''        self.ratio = normalize_ratio(str(resolved["ratio"]))
        self.api_style = (
            "tokendance" if "tokendance.space" in self.base_url.lower() else "volcengine"
        ) if base_url else resolved["api_style"]
        self.poll_interval = max(1, int(resolved["poll_interval"]))
        self.timeout = max(30, int(resolved["timeout"]))
        if not self.base_url:
            raise VideoConfigError("未配置 VIDEO_BASE_URL。")
        if not self.api_key:
            raise VideoConfigError(
                "未配置 VIDEO_API_KEY。图生视频不会在缺少密钥时假装生成成功。"
            )
'''
if old not in s: raise SystemExit('video block missing')
s=s.replace(old,new)
old='''        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]; raise RuntimeError(f"TokenDance 图生视频 HTTP {exc.code}: {detail}") from exc
        if not isinstance(payload, dict): raise RuntimeError("TokenDance 图生视频返回格式不是 JSON 对象")
'''
new='''        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"TokenDance 图生视频 HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"TokenDance 图生视频请求未完成：{exc.reason}") from exc
        except TimeoutError as exc:
            raise RuntimeError("TokenDance 图生视频请求超时；未自动重试，避免重复提交") from exc
        if not isinstance(payload, dict): raise RuntimeError("TokenDance 图生视频返回格式不是 JSON 对象")
'''
if old not in s: raise SystemExit('request block missing')
s=s.replace(old,new); p.write_text(s,encoding='utf-8')
print('patched')
