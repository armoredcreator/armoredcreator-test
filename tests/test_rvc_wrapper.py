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

        class FakeRVCInference:
            def __init__(self, **_kwargs):
                pass

            def infer_file(self, _input, output):
                if output is not None and result == "write":
                    Path(output).write_bytes(output_bytes)
                return result

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

    def test_accepts_library_that_writes_output_and_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "input.wav"
            output = root / "output.wav"
            source.write_bytes(b"input")

            modules = self._install_fake_rvc("write")
            with patch.dict(sys.modules, modules):
                converter_interno(source, output, root / "model.pth", None)

            self.assertEqual(output.read_bytes(), b"wav")

    def test_rejects_malformed_tuple_instead_of_scipy_dtype_error(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "input.wav"
            output = root / "output.wav"
            source.write_bytes(b"input")

            modules = self._install_fake_rvc(("conversion failed", "not-audio"))
            with patch.dict(sys.modules, modules):
                with self.assertRaisesRegex(RuntimeError, "resultado inválido"):
                    converter_interno(source, output, root / "model.pth", None)

            self.assertFalse(output.exists())

    def test_accepts_valid_tuple_result(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "input.wav"
            output = root / "output.wav"
            source.write_bytes(b"input")

            class Audio:
                dtype = "float32"

            modules = self._install_fake_rvc((44100, Audio()))
            with patch.dict(sys.modules, modules):
                converter_interno(source, output, root / "model.pth", None)

            self.assertEqual(output.read_bytes(), b"wav")


if __name__ == "__main__":
    unittest.main()
