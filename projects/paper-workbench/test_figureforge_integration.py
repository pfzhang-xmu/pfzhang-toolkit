import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from web import imagegen_bridge


PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20


class FigureForgeIntegrationTests(unittest.TestCase):
    def test_standalone_save_and_load_preserve_project_json(self):
        project = {"version": 2, "baseImageSrc": "data:image/png;base64,xxx", "objects": [], "holes": []}
        with tempfile.TemporaryDirectory() as td:
            old = imagegen_bridge.STANDALONE_FIGURES_ROOT
            imagegen_bridge.STANDALONE_FIGURES_ROOT = Path(td) / "figures"
            try:
                saved = imagegen_bridge.save_figureforge_version(None, "demo", base64.b64encode(PNG).decode(), project)
                loaded = imagegen_bridge.load_figureforge_version(None, "demo", saved["version"])
                self.assertEqual(saved["version"], "v001")
                self.assertEqual(json.loads(loaded["project_json"]), project)
                self.assertTrue((imagegen_bridge.STANDALONE_FIGURES_ROOT / "demo" / "versions" / "v001.png").exists())
            finally:
                imagegen_bridge.STANDALONE_FIGURES_ROOT = old

    def test_each_save_creates_immutable_version(self):
        project = {"version": 2, "baseImageSrc": "data:image/png;base64,xxx", "objects": [], "holes": []}
        with tempfile.TemporaryDirectory() as td:
            old = imagegen_bridge.STANDALONE_FIGURES_ROOT
            imagegen_bridge.STANDALONE_FIGURES_ROOT = Path(td) / "figures"
            try:
                first = imagegen_bridge.save_figureforge_version(None, "demo", base64.b64encode(PNG).decode(), project)
                second = imagegen_bridge.save_figureforge_version(None, "demo", base64.b64encode(PNG).decode(), project)
                self.assertEqual((first["version"], second["version"]), ("v001", "v002"))
            finally:
                imagegen_bridge.STANDALONE_FIGURES_ROOT = old


if __name__ == "__main__":
    unittest.main()
