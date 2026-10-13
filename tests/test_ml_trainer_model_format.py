"""Regression tests for the XGBoost model serialization path.

XGBoost 3.x picks the on-disk format from the file extension. The canonical
artifact is now ``models/forward_return.ubj``; the legacy ``.xgb`` name
raised "Saving model in the UBJSON format as default" on save and
"Unknown file format: `xgb`" on load. These tests lock the new behaviour
and the legacy fallback. No DB, no IO outside ``tmp_path``.
"""

import warnings

import numpy as np
import xgboost as xgb

from myra_app.ml_trainer import MLTrainer


def _tiny_model():
    rng = np.random.RandomState(0)
    X = rng.rand(40, 4)
    y = rng.randint(0, 3, size=40)
    model = xgb.XGBClassifier(n_estimators=2, max_depth=2, verbosity=0)
    model.fit(X, y)
    return model


def test_canonical_model_path_is_ubj():
    assert MLTrainer.MODEL_PATH.endswith(".ubj")
    assert MLTrainer.LEGACY_MODEL_PATH.endswith(".xgb")


def test_ubj_save_load_roundtrip_emits_no_warning(tmp_path):
    path = tmp_path / "forward_return.ubj"
    model = _tiny_model()
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        model.save_model(str(path))
        reloaded = xgb.XGBClassifier()
        reloaded.load_model(str(path))

    format_warnings = [
        w
        for w in captured
        if "file format" in str(w.message).lower() or "ubjson" in str(w.message).lower()
    ]
    assert format_warnings == [], [str(w.message) for w in format_warnings]


def test_resolve_model_path_none_when_no_artifact(tmp_path, monkeypatch):
    monkeypatch.setattr(MLTrainer, "MODEL_PATH", str(tmp_path / "missing.ubj"))
    monkeypatch.setattr(MLTrainer, "LEGACY_MODEL_PATH", str(tmp_path / "missing.xgb"))
    trainer = MLTrainer({"dummy": True})
    assert trainer._resolve_model_path() is None
    assert trainer._load_model() is False
    assert trainer.get_status()["exists"] is False


def test_resolve_model_path_prefers_ubj_then_legacy(tmp_path, monkeypatch):
    ubj = tmp_path / "forward_return.ubj"
    legacy = tmp_path / "forward_return.xgb"
    _tiny_model().save_model(str(ubj))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        _tiny_model().save_model(str(legacy))

    monkeypatch.setattr(MLTrainer, "MODEL_PATH", str(ubj))
    monkeypatch.setattr(MLTrainer, "LEGACY_MODEL_PATH", str(legacy))
    assert MLTrainer({"dummy": True})._resolve_model_path() == str(ubj)

    # Only the legacy artifact present -> fallback is used.
    ubj.unlink()
    trainer = MLTrainer({"dummy": True})
    assert trainer._resolve_model_path() == str(legacy)
    with warnings.catch_warnings():
        # Loading the legacy '.xgb' name intentionally still warns; this test
        # only asserts the fallback path is reached.
        warnings.simplefilter("ignore")
        assert trainer._load_model() is True


def test_load_model_loads_ubj_artifact(tmp_path, monkeypatch):
    path = tmp_path / "forward_return.ubj"
    _tiny_model().save_model(str(path))
    monkeypatch.setattr(MLTrainer, "MODEL_PATH", str(path))
    monkeypatch.setattr(MLTrainer, "LEGACY_MODEL_PATH", str(tmp_path / "absent.xgb"))
    trainer = MLTrainer({"dummy": True})
    assert trainer._load_model() is True
    assert trainer.model is not None
