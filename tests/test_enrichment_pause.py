"""Phase 1 tests — cooperative enrichment cancel/pause checkpoints."""

from __future__ import annotations

import threading
import time

import pytest

from myra_app.feature_enrichment import EnrichmentCancelled, _checkpoint


def test_checkpoint_is_noop_without_events():
    # Must not raise or block.
    _checkpoint(None, None)


def test_checkpoint_raises_when_cancelled():
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(EnrichmentCancelled):
        _checkpoint(cancel, None)


def test_checkpoint_blocks_while_paused_then_proceeds():
    pause = threading.Event()
    pause.clear()  # paused
    done = [False]

    def worker():
        _checkpoint(None, pause)
        done[0] = True

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    time.sleep(0.2)
    assert not done[0], "checkpoint should block while paused"

    pause.set()  # resume
    t.join(timeout=2)
    assert done[0], "checkpoint should proceed after resume"


def test_checkpoint_cancels_while_paused():
    pause = threading.Event()
    pause.clear()
    cancel = threading.Event()
    raised = []

    def worker():
        try:
            _checkpoint(cancel, pause)
        except EnrichmentCancelled:
            raised.append(True)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    time.sleep(0.1)
    cancel.set()  # cancel while paused must unblock and raise
    t.join(timeout=2)
    assert raised, "checkpoint must raise when cancelled while paused"


def test_process_enrichment_pipeline_accepts_control_events():
    import inspect

    from myra_app.feature_enrichment import process_enrichment_pipeline

    params = inspect.signature(process_enrichment_pipeline).parameters
    assert "cancel_event" in params
    assert "pause_event" in params
    assert "raise_on_error" in params
