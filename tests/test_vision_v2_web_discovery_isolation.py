from __future__ import annotations

from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_vision_v2_web_discovery.py"


def test_web_discovery_experiment_is_isolated_from_v1_and_v2():
    source = SCRIPT.read_text(encoding="utf-8")

    assert "ArmoredVision.modules.v1" not in source
    assert "ArmoredVision.modules.v2" not in source
    assert "CandidateReconciler" not in source
    assert "CLIP" not in source
    assert "SIFT" not in source


def test_web_discovery_experiment_contains_known_benchmark_cases():
    source = SCRIPT.read_text(encoding="utf-8")

    assert "1609734117:22794532266" in source
    assert "329536801:28939276497" in source
    assert "1168408423:22192826116" in source
    assert "1262524556:21299262872" in source


def test_web_discovery_experiment_does_not_make_identity_decisions():
    source = SCRIPT.read_text(encoding="utf-8")

    assert "ACCEPT" not in source
    assert "REJECT" not in source
    assert "reconciler" in source
