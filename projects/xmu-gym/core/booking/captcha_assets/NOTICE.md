# Captcha classifier attribution

Model and character index originate from ddddocr 1.6.1 (sml2h3/ddddocr).
Original license is retained in LICENSE. The production worker includes only
the default classification model common_old.onnx, ASCII output indices and
a small classification-only ONNX Runtime / Pillow / NumPy adapter.
The model and normalization match the previous worker's DdddOcr(beta=False).

Model SHA256: b8f2ad9cbc1f2e3922a6cb9459e30824e7e2467f3fb4fd61420640e34ea0bf68
