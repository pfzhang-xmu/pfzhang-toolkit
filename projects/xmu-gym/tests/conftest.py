import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("XMU_GYM_STATE", tempfile.mkdtemp(prefix="xmu-gym-tests-"))
