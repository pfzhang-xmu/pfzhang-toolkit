"""No network: preserve the classification model and CTC filtering semantics."""
import hashlib
from pathlib import Path

from core.booking.captcha_ocr import decode_ctc


def test_ctc_filters_after_collapsing_indices():
    # A blank or an excluded class between equal characters prevents collapse.
    assert decode_ctc([1, 1, 0, 1, 99, 1, 2, 2], {1: "a", 2: "B"}) == "aaaB"
    assert decode_ctc([], {}) == ""


def test_uses_exact_original_default_captcha_model():
    model = Path(__file__).parents[2] / "core/booking/captcha_assets/common_old.onnx"
    assert hashlib.sha256(model.read_bytes()).hexdigest() == "b8f2ad9cbc1f2e3922a6cb9459e30824e7e2467f3fb4fd61420640e34ea0bf68"
