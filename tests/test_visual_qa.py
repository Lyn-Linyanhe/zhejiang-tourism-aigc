"""视觉验收必须看像素，不能只看 media_type 或 provider 自报。"""

import tempfile
import unittest
from pathlib import Path

from backend.app.services import consistency, visual_qa


def _write_scene(path: Path, shift: int = 0, flat: bool = False) -> None:
    width, height = 480, 640
    if flat:
        pixels = bytes((40, 80, 110)) * (width * height)
    else:
        rows = []
        for y in range(height):
            for x in range(width):
                red = (x * 255 // width + shift * 7) % 256
                green = (y * 255 // height + shift * 11) % 256
                blue = ((x * 3 + y * 5 + shift * 13) % 256)
                rows.append(bytes((red, green, blue)))
        pixels = b"".join(rows)
    path.write_bytes(f"P6\n{width} {height}\n255\n".encode("ascii") + pixels)


class VisualQaTests(unittest.TestCase):
    def test_flat_image_fails_objective_quality_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flat.ppm"
            _write_scene(path, flat=True)
            result = visual_qa.verify_media_file(path)
            self.assertFalse(result["passed"])
            self.assertIn("画面对比度过低", result["failures"])

    def test_four_video_shots_pass_and_three_fail_the_acceptance_threshold(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "subject.ppm"
            _write_scene(reference, shift=2)
            shots = []
            assets = []
            for order in range(1, 5):
                path = root / f"shot_{order}.ppm"
                _write_scene(path, shift=order)
                media_type = "image" if order == 4 else "video"
                shots.append({
                    "id": f"shot_{order}", "order": order,
                    "visual_prompt": f"西湖第{order}个不同镜头",
                    "asset_local_path": str(path), "asset_media_type": media_type,
                })
                assets.append({"shot_id": f"shot_{order}", "source": "pexels"})
            task = {
                "id": "task_qa",
                "input": {
                    "city": "杭州", "landmark": "西湖", "theme": "城市漫游",
                    "style": "诗意纪实", "subject_lock": "同一座断桥与湖岸轮廓",
                    "subject_reference_path": str(reference), "output_resolution": "720x1280",
                },
                "audio": {"knowledge": {"color": "#2d6c63", "accent": "#d6ad60"}},
                "shots": shots, "assets": assets,
            }
            report = visual_qa.verify_task_visuals(
                task, min_video_shots=4, strict_media=True, strict_reference=False
            )
            self.assertFalse(report["passed"])
            self.assertEqual(report["requirements"]["video_shot_count"], 3)
            self.assertTrue(any("视频镜头 3 个" in item for item in report["failures"]))
            self.assertTrue(report["generation_contract"]["passed"])

            shots[3]["asset_media_type"] = "video"
            report = visual_qa.verify_task_visuals(
                task, min_video_shots=4, strict_media=True, strict_reference=False
            )
            self.assertTrue(report["passed"], report["failures"])
            self.assertEqual(report["requirements"]["video_shot_count"], 4)
            self.assertEqual(report["generation_contract"]["anchored_shot_count"], 4)

    def test_contract_validation_rejects_missing_subject_anchor(self):
        plan = consistency.plan(
            "task_contract",
            [{"id": "shot_1", "order": 1, "visual_prompt": "西湖晨雾"}],
            {"city": "杭州", "landmark": "西湖", "subject_lock": "同一座断桥"},
        )
        plan["shots"][0]["prompt"] = "西湖晨雾，普通风景照"
        result = consistency.validate_plan(plan)
        self.assertFalse(result["passed"])
        self.assertTrue(any("主体 ID" in item for item in result["failures"]))


if __name__ == "__main__":
    unittest.main()
