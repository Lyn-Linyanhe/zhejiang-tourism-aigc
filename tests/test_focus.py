import tempfile
import unittest
from pathlib import Path

from backend.app.providers.focus import detect_focus


class FocusTests(unittest.TestCase):
    def test_prominent_off_center_subject_is_not_treated_as_center(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "subject.ppm"
            pixels = bytearray([230, 230, 230] * (160 * 90))
            for y in range(25, 66):
                for x in range(115, 135):
                    at = (y * 160 + x) * 3
                    pixels[at:at + 3] = bytes((18, 25, 38))
            path.write_bytes(b"P6\n160 90\n255\n" + pixels)
            x, _ = detect_focus(path)
            self.assertGreater(x, 0.65)

    def test_flat_image_uses_center(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flat.ppm"
            path.write_bytes(b"P6\n16 9\n255\n" + bytes((128, 128, 128)) * 16 * 9)
            self.assertEqual(detect_focus(path), (0.5, 0.5))
