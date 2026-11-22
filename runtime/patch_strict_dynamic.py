from pathlib import Path
p=Path(r'C:\Users\ev041\Desktop\人工智能\zhejiang-tourism-aigc-merged\backend\app\services\orchestrator.py')
s=p.read_text(encoding='utf-8')
start=s.index('                failures: list[str] = []', s.index('def _assets'))
end=s.index('            shot.asset_url = asset["url"]', start)
replacement='''                failures: list[str] = []
                asset = None
                if strict_dynamic and "ai-video" in candidates:
                    frame = self._ensure_first_frame(
                        task, shot, shot_payload, directory, knowledge, style_ctx, allow_offline=False
                    )
                    if not frame:
                        raise RuntimeError(f"Shot {shot.order} did not produce a usable AI first frame")
                    shot_payload["first_frame_path"] = frame
                    try:
                        asset = self.registry.generate_asset(
                            "ai-video", task.id, shot_payload, directory, style_ctx
                        )
                        used_sources.add("ai-video")
                    except Exception as exc:
                        raise RuntimeError(
                            f"Shot {shot.order} image-to-video failed after first frame generation: {exc}"
                        ) from exc
                else:
                    if "ai-video" in candidates:
                        frame = self._ensure_first_frame(
                            task, shot, shot_payload, directory, knowledge, style_ctx
                        )
                        if frame:
                            shot_payload["first_frame_path"] = frame
                    for candidate in candidates:
                        try:
                            asset = self.registry.generate_asset(
                                candidate, task.id, shot_payload, directory, style_ctx
                            )
                            used_sources.add(candidate)
                            if failures and task.trace:
                                task.trace.fallback_used = True
                                task.trace.events.append({
                                    "type": "provider_fallback", "slot": "asset",
                                    "shot_id": shot.id, "provider": candidate,
                                    "fallback_reason": "; ".join(failures),
                                })
                            break
                        except Exception as exc:
                            failures.append(f"{candidate}: {exc}")
                if asset is None:
                    raise RuntimeError(f"No asset provider succeeded for shot {shot.order}: {'; '.join(failures)}")
'''
s=s[:start]+replacement+s[end:]
# Add strict flag and per-shot progress if not present.
s=s.replace('        used_sources: set[str] = set()\n        for shot in task.shots:', '        used_sources: set[str] = set()\n        strict_dynamic = asset_source in {"ai", "cloud", "ai-video", "cloud-i2v"}\n        for shot_index, shot in enumerate(task.shots):\n            self.update(task, stage="assets", progress=min(75, 67 + int(8 * shot_index / max(len(task.shots), 1))), event={"type": "shot", "stage": "assets", "shot_id": shot.id, "status": "started", "order": shot.order})')
s=s.replace('        style_ctx: dict[str, Any],\n    ) -> str:\n', '        style_ctx: dict[str, Any],\n        allow_offline: bool = True,\n    ) -> str:\n', 1)
s=s.replace('            acquired = self.registry.acquire_asset(\n                ["ai-image", "offline"],', '            frame_chain = ["ai-image"] + (["offline"] if allow_offline else [])\n            acquired = self.registry.acquire_asset(\n                frame_chain,', 1)
p.write_text(s,encoding='utf-8')