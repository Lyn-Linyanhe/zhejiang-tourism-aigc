from pathlib import Path
root=Path(r"C:\Users\ev041\Desktop\人工智能\zhejiang-tourism-aigc-merged")
p=root/'backend/app/providers/video.py'; s=p.read_text(encoding='utf-8-sig'); s=s.replace('import base64\n','import base64\nimport hashlib\n')
a=s.index('    def _generate_tokendance('); b=s.index('    def _tokendance_request(',a)
s=s[:a]+'''    def _generate_tokendance(self, shot, frame_path, output_dir, prompt, seed, duration, resolution):
        target = Path(output_dir)
        target.mkdir(parents=True, exist_ok=True)
        shot_id = str(shot.get("id") or f"shot_{shot.get('order', 1)}")
        if Path(shot_id).name != shot_id or shot_id in {".", ".."}:
            raise ValueError("Invalid shot id")
        receipt = target / f"{shot_id}.remote-task.json"
        body = {"model": self.model, "resolution": str(resolution).upper(), "duration": duration, "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image_to_data_url(frame_path)}, "role": "first_frame"},
        ]}
        fingerprint = hashlib.sha256(json.dumps({"endpoint": self.base_url, "body": body}, sort_keys=True).encode()).hexdigest()
        saved = {}
        if receipt.is_file():
            try:
                saved = json.loads(receipt.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise VideoSubmitUnconfirmed("Remote task receipt is unreadable; submission was not repeated") from exc
            if not isinstance(saved, dict) or not saved.get("remote_task_id") or saved.get("fingerprint") != fingerprint:
                raise VideoSubmitUnconfirmed("Existing remote task receipt does not match this input; submission was not repeated")
        else:
            submitted = self._tokendance_request(f"{self.base_url}/video_generation", "POST", body)
            task_id = str(submitted.get("task_id") or submitted.get("id") or "")
            if not task_id:
                raise VideoSubmitUnconfirmed("TokenDance 图生视频提交未返回 task_id")
            saved = {"remote_task_id": task_id, "provider": self.name, "shot_id": shot_id, "fingerprint": fingerprint, "status": "submitted"}
            receipt.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
        task_id = str(saved["remote_task_id"])
        deadline = time.monotonic() + self.timeout
        video_url = ""
        while time.monotonic() < deadline:
            time.sleep(self.poll_interval)
            payload = self._tokendance_request(f"{self.base_url}/query/video_generation/{urllib.parse.quote(task_id, safe='')}", "GET", None)
            task = payload.get("task") if isinstance(payload.get("task"), dict) else payload
            status = str(task.get("status") or "").lower()
            saved.update(status=status, last_polled_at=time.time(), response_fields=sorted(task))
            receipt.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
            if status == "succeeded":
                content = task.get("content")
                video_url = str((content.get("url") or content.get("video_url")) if isinstance(content, dict) else "") or str(task.get("url") or "")
                if not video_url:
                    raise VideoDownloadError(f"TokenDance task {task_id} succeeded but returned no video URL")
                break
            if status in TERMINAL_FAILURE:
                raise RuntimeError(f"TokenDance 图生视频失败: {status} {task.get('error') or ''}".strip())
            if status not in ACTIVE_STATUS:
                raise VideoSubmitUnconfirmed(f"TokenDance task {task_id} returned unrecognized status {status!r}; response fields: {sorted(task)}")
        if not video_url:
            raise VideoSubmitUnconfirmed(f"TokenDance 图生视频超时，远端编号已保存，可查询同一任务: {task_id}")
        dest = target / f"{shot_id}.mp4"
        download_video(video_url, dest)
        result = AssetResult(path=str(dest), media_type=MEDIA_TYPE_VIDEO, source=self.name, model=self.model, seed=seed, prompt=prompt, first_frame_path=str(frame_path), cost=float(self.cost_per_call), event={"type":"video", "provider":self.name, "api_style":"tokendance", "remote_task_id":task_id, "duration":duration, "resolution":resolution})
        result.validate()
        saved.update(status="downloaded", local_path=str(dest))
        receipt.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
        return result

'''+s[b:]; p.write_text(s,encoding='utf-8')
p=root/'backend/app/services/orchestrator.py'; s=p.read_text(encoding='utf-8')
s=s.replace('if existing and existing.local_path and Path(existing.local_path).exists():','if existing and existing.local_path and Path(existing.local_path).exists() and (not strict_dynamic or Path(existing.local_path).suffix.lower() in {".mp4", ".mov", ".m4v", ".webm"}):')
s=s.replace('existing = str(payload.get("first_frame_path") or "")','existing = str(payload.get("first_frame_path") or payload.get("asset_first_frame_path") or "")')
s=s.replace('        shot.asset_local_path = frame\n        return frame','        shot.asset_local_path = frame\n        shot.asset_first_frame_path = frame\n        self.update(task, event={"type": "first_frame", "shot_id": shot.id, "status": "ready", "provider": acquired.provider})\n        return frame')
p.write_text(s,encoding='utf-8')
print('receipt fingerprint, poll diagnostics, first-frame persistence added')
