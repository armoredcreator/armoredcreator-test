from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from ArmoredStudio.processing.rvc import converter_interno


class RvcWrapperTests(unittest.TestCase):
    def _install_fake_rvc(self, result, output_bytes=b"wav"):
        infer_module = types.ModuleType("rvc_python.infer")

        class FakeVC:
            tgt_sr = 44100

            def vc_single(self, **_kwargs):
                return result

        class FakeRVCInference:
            def __init__(self, **_kwargs):
                self.models = {"model.pth": {"index": "model.index"}}
                self.current_model = "model.pth"
                self.f0up_key = 0
                self.f0method = "harvest"
                self.index_rate = 0.5
                self.filter_radius = 3
                self.resample_sr = 0
                self.rms_mix_rate = 1
                self.protect = 0.33
                self.vc = FakeVC()

        infer_module.RVCInference = FakeRVCInference

        wavfile_module = types.ModuleType("scipy.io.wavfile")
        wavfile_module.write = lambda path, _rate, _audio: Path(path).write_bytes(output_bytes)
        scipy_io = types.ModuleType("scipy.io")
        scipy_io.wavfile = wavfile_module
        scipy = types.ModuleType("scipy")
        scipy.io = scipy_io

        return {
            "rvc_python": types.ModuleType("rvc_python"),
            "rvc_python.infer": infer_module,
            "scipy": scipy,
            "scipy.io": scipy_io,
            "scipy.io.wavfile": wavfile_module,
        }

    def test_accepts_library_audio_array_result(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "input.wav"
            output = root / "output.wav"
            source.write_bytes(b"input")

            class Audio:
                dtype = "float32"

            modules = self._install_fake_rvc(Audio())
            with patch.dict(sys.modules, modules):
                converter_interno(source, output, root / "model.pth", None)

            self.assertEqual(output.read_bytes(), b"wav")

    def test_rejects_backend_traceback_tuple_without_scipy_dtype_error(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "input.wav"
            output = root / "output.wav"
            source.write_bytes(b"input")

            modules = self._install_fake_rvc(
                ("Traceback (most recent call last):\\nKeyboardInterrupt", (None, None))
            )
            with patch.dict(sys.modules, modules):
                with self.assertRaisesRegex(RuntimeError, "RVC inference falhou no backend"):
                    converter_interno(source, output, root / "model.pth", None)

            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
