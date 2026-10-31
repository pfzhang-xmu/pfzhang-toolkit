"""Classification-only CPU OCR. No OpenCV, detector, slider engine or hooks.

Uses the same common_old.onnx model and PIL/CTC pipeline as ddddocr 1.6.1's
default classifier. See captcha_assets/NOTICE.md and LICENSE for model attribution.
"""
from __future__ import annotations
import io
import json
from pathlib import Path


def decode_ctc(indices, charset: dict[int, str]) -> str:
    result = []
    previous = None
    for value in indices:
        index = int(value)
        if index != previous and index != 0 and index in charset:
            result.append(charset[index])
        previous = index
    return "".join(result)


class CaptchaClassifier:
    def __init__(self):
        import onnxruntime as ort
        assets = Path(__file__).with_name("captcha_assets")
        self.charset = {int(k): v for k, v in json.loads((assets / "ascii.json").read_text(encoding="utf-8")).items()}
        options = ort.SessionOptions()
        options.log_severity_level = 3
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(assets / "common_old.onnx"), sess_options=options,
                                           providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name

    def warm_inference(self):
        import numpy as np
        # Exercise CPU graph/allocation locally; this is not a server captcha.
        self.session.run(None,{self.input_name:np.zeros((1,1,64,160),dtype=np.float32)})

    def classification(self, content: bytes) -> str:
        import numpy as np
        from PIL import Image
        with Image.open(io.BytesIO(content)) as source:
            if source.width < 1 or source.height < 1 or source.width * source.height > 4_000_000:
                raise ValueError("invalid captcha dimensions")
            image = source.resize((max(1, int(source.width * 64 / source.height)), 64),
                                  Image.Resampling.LANCZOS).convert("L")
            tensor = (np.asarray(image, dtype=np.float32) / 255.0)[None, None, :, :]
        output = self.session.run(None, {self.input_name: tensor})[0]
        if output.ndim == 3:
            output = output[:, 0, :] if output.shape[1] == 1 else output[0, :, :]
        indices = np.asarray(output.argmax(axis=-1)).reshape(-1)
        return decode_ctc(indices, self.charset)
