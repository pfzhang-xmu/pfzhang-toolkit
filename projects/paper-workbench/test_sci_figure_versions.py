#!/usr/bin/env python3
"""Focused offline tests for the科研绘图版本/reference workflow."""
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from web import imagegen_bridge as ig


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class SciFigureVersionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "paper"
        self.project.mkdir()
        (self.project / "state.json").write_text("{}", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_versions_are_immutable_and_current_is_explicit(self):
        one = ig.save_version(self.project, "fig1_pathway", base64.b64encode(PNG_1X1).decode(), "png")
        two = ig.save_version(self.project, "fig1_pathway", base64.b64encode(PNG_1X1).decode(), "png")
        self.assertEqual((one["version"], two["version"]), ("v001", "v002"))
        self.assertNotEqual(one["rel"], two["rel"])
        info = ig.list_asset_versions(self.project, "fig1_pathway")
        self.assertEqual(info["current"], "")
        ig.set_current_version(self.project, "fig1_pathway", "v002")
        self.assertEqual(ig.list_asset_versions(self.project, "fig1_pathway")["current"], "v002")

    def test_reference_upload_is_reusable_and_non_overwriting(self):
        first = ig.save_reference(self.project, "fig1", PNG_1X1, "source.png")
        second = ig.save_reference(self.project, "fig1", PNG_1X1, "source.png")
        self.assertEqual((first["reference_id"], second["reference_id"]), ("ref001", "ref002"))
        info = ig.list_asset_versions(self.project, "fig1")
        self.assertEqual([x["reference_id"] for x in info["references"]], ["ref001", "ref002"])
        self.assertTrue(ig.resolve_asset_image(self.project, "fig1", reference_id="ref001").exists())

    def test_edit_request_uses_multipart_edits_endpoint(self):
        reference = self.project / "ref.png"
        reference.write_bytes(PNG_1X1)
        captured = {}

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self):
                return json.dumps({"data": [{"b64_json": base64.b64encode(PNG_1X1).decode()}]}).encode()

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["type"] = req.headers.get("Content-type")
            captured["body"] = req.data
            return Response()

        cfg = {"image": {"base_url": "https://example.test/v1", "api_key": "key", "model": "gpt-image-2"}}
        with patch.object(ig, "load_config", return_value=(cfg, {**ig.DEFAULT_IMAGE, **cfg["image"]})), \
             patch.object(ig.urllib.request, "urlopen", side_effect=fake_urlopen):
            result = ig.edit_image("change the right panel", reference)
        self.assertTrue(result["ok"])
        self.assertEqual(captured["url"], "https://example.test/v1/images/edits")
        self.assertIn("multipart/form-data", captured["type"])
        self.assertIn(b'name="image"', captured["body"])
        self.assertIn(b"change the right panel", captured["body"])

    def test_unknown_model_is_rejected_without_request(self):
        cfg = {"image": {"base_url": "https://example.test/v1", "api_key": "key", "model": "unknown-model"}}
        with patch.object(ig, "load_config", return_value=(cfg, {**ig.DEFAULT_IMAGE, **cfg["image"]})):
            result = ig.edit_image("change", self.project / "missing.png")
        self.assertFalse(result["ok"])
        self.assertIn("/images/edits", result["error"])


if __name__ == "__main__":
    unittest.main()
