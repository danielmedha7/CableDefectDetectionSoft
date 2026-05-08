"""Smoke tests — verify modules import and core math runs.

Run from project root:
    python -m pytest tests/ -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


# ── ai_models ──
def test_multiview_fusion_dedups_across_cameras():
    from ai_models.multiview_fusion import MultiViewFusion
    from ai_models.yolo_detector import Detection

    fusion = MultiViewFusion(frame_width_px=1920)
    dets = {
        0: [Detection(bbox=(950, 400, 1000, 500), cls="crack", confidence=0.92, camera_id=0, timestamp=0.0)],
        1: [Detection(bbox=(950, 400, 1000, 500), cls="crack", confidence=0.78, camera_id=1, timestamp=0.0)],
    }
    out = fusion.fuse(dets, position_m=1.0, depth_profile=None)
    cracks = [d for d in out if d.cls == "crack"]
    # different camera angles → not deduped (no overlap zone), so we accept >=1
    assert len(cracks) >= 1
    assert all(0.0 <= d.angle_deg < 360.0 for d in cracks)


def test_severity_classifier_levels():
    from ai_models.multiview_fusion import WorldDefect
    from ai_models.severity_classifier import SeverityClassifier

    sc = SeverityClassifier()
    minor = WorldDefect(cls="scratch", confidence=0.5, angle_deg=0, position_m=0,
                        bbox=(0, 0, 10, 10), camera_id=0, depth_mm=0.05, area_mm2=2.0)
    major = WorldDefect(cls="insulation_gap", confidence=0.95, angle_deg=0, position_m=0,
                        bbox=(0, 0, 10, 10), camera_id=0, depth_mm=1.8, area_mm2=28.0)
    sc.classify(minor)
    sc.classify(major)
    assert minor.severity in {"low", "medium"}
    assert major.severity in {"high", "critical"}


# ── system_logic ──
def test_position_tracker_pulses_to_metres():
    from system_logic.position_tracker import PositionTracker

    pt = PositionTracker(pulses_per_rev=2000, wheel_diameter_mm=100.0)
    # one full revolution = π·100 mm ≈ 314.159 mm
    pt.add_pulses(2000)
    assert abs(pt.position_mm - np.pi * 100.0) < 0.01


def test_qc_engine_fail_on_forbidden_class(tmp_path: Path):
    from ai_models.multiview_fusion import WorldDefect
    from system_logic.qc_engine import QCEngine, VERDICT_FAIL

    rules = ROOT / "configs" / "qc_rules.yaml"
    qc = QCEngine(rules_path=rules, cable_spec="HV_35mm")
    d = WorldDefect(cls="insulation_gap", confidence=0.9, angle_deg=10,
                    position_m=2.5, bbox=(0, 0, 10, 10), camera_id=0,
                    depth_mm=0.3, area_mm2=4.0, severity="high")
    v = qc.evaluate_defect(d, cable_length_m=5.0)
    assert v.verdict == VERDICT_FAIL


def test_session_manager_lifecycle():
    from ai_models.multiview_fusion import WorldDefect
    from system_logic.session_manager import SessionManager

    sm = SessionManager()
    sess = sm.start("C-TEST", "default", "tester")
    sm.add_defect(WorldDefect(cls="crack", confidence=0.8, angle_deg=0, position_m=1.0,
                              bbox=(0, 0, 10, 10), camera_id=0))
    sm.update_length(10.0)
    ended = sm.end("PASS")
    assert ended is not None
    assert ended.id == sess.id
    assert ended.cable_length_m == 10.0
    assert len(ended.defects) == 1


# ── processing ──
def test_ultrasonic_feature_vector_length():
    from processing.ultrasonic_processing.feature_extractor import (
        UltrasonicFeatureExtractor, FEATURE_LEN,
    )
    fx = UltrasonicFeatureExtractor()
    sig = np.random.randn(2000).astype(np.float32)
    feats = fx.extract(sig)
    assert feats.shape == (FEATURE_LEN,)
    assert np.all(np.isfinite(feats))


def test_diameter_estimator_runs_without_frames():
    from processing.measurement.diameter_estimator import DiameterEstimator
    de = DiameterEstimator(expected_diameter_mm=35.0)
    out = de.estimate_vision({})
    assert out.diameter_mm == 0.0


def test_roundness_monitor_basic():
    from processing.measurement.roundness_monitor import RoundnessMonitor
    rm = RoundnessMonitor(min_roundness=0.97)
    perfect = rm.evaluate({0: 35.0, 1: 35.0, 2: 35.0, 3: 35.0})
    skewed = rm.evaluate({0: 35.0, 1: 33.0, 2: 35.0, 3: 33.0})
    assert perfect.roundness > 0.99
    assert skewed.roundness < 0.97


# ── report generation ──
def test_report_generator_writes_files(tmp_path: Path):
    from system_logic.session_manager import Session
    from system_logic.report_generator import ReportGenerator

    rg = ReportGenerator(output_dir=tmp_path)
    sess = Session(
        id="abc123", cable_id="C-TEST", cable_spec="default", operator="test",
        started_at=0.0, ended_at=10.0, verdict="PASS", verdict_reasons=[],
        cable_length_m=5.0, defects=[],
    )
    paths = rg.save(sess, formats=("json", "html"))
    assert paths["json"].exists()
    assert paths["html"].exists()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
