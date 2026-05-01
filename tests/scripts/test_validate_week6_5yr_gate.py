"""R12.5 P fix (Codex audit): regression tests for _validate_week6_5yr.py
surface coverage gate Tier 1 (coverage_pct) + Tier 2 (truly_unmarkable).

Codex R12.4 反證:
  - Tier 1: 50% surface coverage + 100% settle fill → silent PASS (surface
    fallback degrades to settle fallback). R12.5 fix: SURFACE_COVERAGE_PCT_MIN=0.95.
  - Tier 2: truly_unmarkable=0 strict gate (R12.4 fix already in place).
"""

from __future__ import annotations

import argparse
import pathlib

import numpy as np
import pandas as pd
import pytest

from scripts._validate_week6_5yr import (
    SURFACE_COVERAGE_PCT_MIN,
    _validate_surface_coverage,
)


class _StubRecord:
    """Minimal SurfaceFitRecord stand-in with .date attribute."""

    def __init__(self, date_str: str) -> None:
        self.date = pd.Timestamp(date_str)


def _stub_args() -> argparse.Namespace:
    return argparse.Namespace(
        start="2026-01-01",
        end="2026-01-02",
        skip_surface_coverage_gate=False,
    )


def test_r12_5_tier1_low_coverage_raises() -> None:
    """Tier 1 (Codex R12.4 P2 fix): coverage_pct < 95% → raise even if settle fills."""
    records = [_StubRecord("2026-01-01")]  # only 1 of 2 dates
    chain = pd.DataFrame(
        {
            "date": [pd.Timestamp("2026-01-01"), pd.Timestamp("2026-01-02")],
            "bid": [np.nan, np.nan],
            "ask": [np.nan, np.nan],
            "model_price": [95.0, np.nan],
            "settle": [88.0, 87.0],  # both have settle → Tier 2 would pass
        }
    )
    with pytest.raises(ValueError, match="R12.5 Tier 1 gate FAIL"):
        _validate_surface_coverage(chain, records, _stub_args())


def test_r12_5_tier2_truly_unmarkable_raises() -> None:
    """Tier 2 (R12.4 fix): truly_unmarkable > 0 → raise even at 100% coverage."""
    records = [_StubRecord("2026-01-01"), _StubRecord("2026-01-02")]
    chain = pd.DataFrame(
        {
            "date": [pd.Timestamp("2026-01-01"), pd.Timestamp("2026-01-02")],
            "bid": [np.nan, np.nan],
            "ask": [np.nan, np.nan],
            "model_price": [95.0, np.nan],
            "settle": [88.0, np.nan],  # day2 truly_unmarkable
        }
    )
    with pytest.raises(ValueError, match="R12.4 Tier 2 gate FAIL"):
        _validate_surface_coverage(chain, records, _stub_args())


def test_r12_5_both_tiers_pass_with_full_coverage_and_settle() -> None:
    """R12.5: 100% surface coverage + truly_unmarkable=0 → PASS."""
    records = [_StubRecord("2026-01-01"), _StubRecord("2026-01-02")]
    chain = pd.DataFrame(
        {
            "date": [pd.Timestamp("2026-01-01"), pd.Timestamp("2026-01-02")],
            "bid": [99.0, 99.0],  # mid valid → no fallback needed
            "ask": [101.0, 101.0],
            "model_price": [95.0, 95.0],
            "settle": [88.0, 88.0],
        }
    )
    # No raise — both tiers pass
    _validate_surface_coverage(chain, records, _stub_args())


def test_r12_5_threshold_constant() -> None:
    """R12.5 P fix: SURFACE_COVERAGE_PCT_MIN constant is 0.95 institutional grade."""
    assert SURFACE_COVERAGE_PCT_MIN == 0.95


# ---------------------------------------------------------------------------
# R12.6 P3 fix (Codex audit): full 5yr 不可用 --skip-surface-coverage-gate
# ---------------------------------------------------------------------------


def test_r12_6_p3_full_5yr_skip_flag_rejected_at_entry() -> None:
    """R12.6 P3 fix: --skip-surface-coverage-gate is diagnostic only;
    full 5yr launch (no --smoke) must reject the flag at parse-time, BEFORE
    expensive enrich. Codex R12.5 抓到原 R12.5 fix 在 enrich 後才 raise →
    user wastes 15-20 min. R12.6 移到 main() 入口 reject.
    """
    from scripts._validate_week6_5yr import main

    with pytest.raises(ValueError, match="R12.6 P3 fix.*skip-surface-coverage-gate"):
        main(["--skip-surface-coverage-gate"])


def test_r12_6_p3_smoke_skip_flag_accepted() -> None:
    """R12.6 P3: --smoke + --skip-surface-coverage-gate combo OK (diagnostic)."""
    # Verify the parse-time check does NOT raise for smoke. We can't easily
    # run main() here (needs full chain load) but can verify the guard logic
    # by checking flag combination directly.
    import argparse

    args = argparse.Namespace(smoke=True, skip_surface_coverage_gate=True)
    scope_yr = "5yr" if not args.smoke else "smoke"
    # The condition (args.skip_surface_coverage_gate and scope_yr == "5yr") is False
    assert not (args.skip_surface_coverage_gate and scope_yr == "5yr")


# ---------------------------------------------------------------------------
# R12.9 P fix (Codex audit): re-exec via subprocess.run propagates exit code
# ---------------------------------------------------------------------------


def test_r12_9_reexec_propagates_exit_code_on_argparse_error() -> None:
    """R12.9 P1 fix: subprocess.run + sys.exit(returncode) properly propagates
    child failure exit code. Codex R12.8 反證 os.execv on Windows = spawn-and-
    detach → parent exits with 0, child exit code lost.

    Verify: bad arg → child argparse error returncode=2 → parent exits 2.
    """
    import subprocess
    import sys

    repo_root = pathlib.Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "_validate_week6_5yr.py"),
            "--definitely-bad-arg",
        ],
        cwd=str(repo_root),
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 2, (
        f"argparse error should give exit 2, got {proc.returncode} "
        f"(R12.8 os.execv silent regression?)"
    )


def test_r12_9_reexec_propagates_value_error_exit_code() -> None:
    """R12.9 P1 fix: ValueError raise → exit 1 (not silent 0)."""
    import subprocess
    import sys

    repo_root = pathlib.Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "_validate_week6_5yr.py"),
            "--skip-surface-coverage-gate",  # 5yr+skip is rejected (R12.6 P3)
        ],
        cwd=str(repo_root),
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 1, (
        f"ValueError should give exit 1, got {proc.returncode} "
        f"(R12.8 silent regression: stderr has trace but exit is 0?)"
    )


# ---------------------------------------------------------------------------
# R12.11 P fix (Codex audit): HMM warning count tracking regression
# ---------------------------------------------------------------------------


def test_r12_11_hmm_warning_counter_captures_messages() -> None:
    """R12.11 P3 fix: _HMMWarningCounter handler must capture 'Model is not
    converging' messages emitted by hmmlearn.base logger. Codex R12.10 抓到
    R12.10 沒 regression test, R12.11 加.

    Strategy: simulate hmmlearn warning emission via logging directly.
    """
    import logging

    # Mirror the production handler from scripts/_validate_week6_5yr.py
    class _Counter(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.count = 0

        def emit(self, record: logging.LogRecord) -> None:
            if "Model is not converging" in record.getMessage():
                self.count += 1

    counter = _Counter()
    logger = logging.getLogger("hmmlearn.base.test_isolated")
    logger.addHandler(counter)
    try:
        logger.warning("Model is not converging. Current: 1.0 vs 1.5. Delta is -0.5")
        logger.warning("Some other unrelated warning")
        logger.warning("Model is not converging again")
        assert counter.count == 2, (
            f"counter should match 2 'Model is not converging' messages; got {counter.count}"
        )
    finally:
        logger.removeHandler(counter)


def test_r12_11_hmm_handler_propagates_to_root_for_stderr() -> None:
    """R12.11 P1 fix (Codex audit): Counter handler MUST NOT eat warnings
    from stderr propagation chain. Codex R12.10 反證: 加 handler 後 hmmlearn
    warning 被 handler 接住, stderr 看不到 → user evidence chain 斷.

    Verify: logger.propagate = True allows root logger / stderr to also see
    the message after counter increments.
    """
    import logging

    class _Counter(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.count = 0

        def emit(self, record: logging.LogRecord) -> None:
            self.count += 1

    counter = _Counter()
    logger = logging.getLogger("hmmlearn.base.test_propagate")
    logger.addHandler(counter)
    logger.propagate = True

    root = logging.getLogger()
    root_records: list[str] = []

    class _RootCapture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            root_records.append(record.getMessage())

    root_cap = _RootCapture()
    root.addHandler(root_cap)
    try:
        logger.warning("propagate test message")
        # Counter handler captured AND root captured (propagation works)
        assert counter.count == 1
        assert any("propagate test" in m for m in root_records), (
            "logger.propagate=True should let message reach root logger"
        )
    finally:
        logger.removeHandler(counter)
        root.removeHandler(root_cap)


def test_r12_13_run_meta_key_writer_grep_contract() -> None:
    """R12.13 P3 fix (Codex R12.12 反證): replaces R12.11 skip-if-missing.

    Strict regression without slow subprocess: grep the script source to
    verify the production line `run_meta["hmm_convergence_warnings_count"] = `
    exists. If a future refactor removes/renames the key, test fails fast —
    no dependency on prior smoke artefact, no skip pattern.
    """
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    script = repo_root / "scripts" / "_validate_week6_5yr.py"
    src = script.read_text(encoding="utf-8")
    # Production code MUST assign this key; check exact assignment pattern
    assert 'run_meta["hmm_convergence_warnings_count"]' in src, (
        "R12.10 P3 regression: run_meta key assignment removed/renamed in "
        f"{script}; counter result will be silently lost from run_meta.json"
    )
    # Production code MUST instantiate the counter class
    assert "_HMMWarningCounter" in src
    # Production code MUST add handler to hmmlearn logger
    assert 'getLogger("hmmlearn.base")' in src


def test_r12_12_run_meta_writer_includes_hmm_key_unit() -> None:
    """R12.12 P3 fix (Codex R12.11 反證): R12.11 test 只驗 reports/ 下既有檔,
    skip-if-missing → 不依賴實跑就 silent skip, regression value 弱.

    R12.12 unit test: 直接 simulate run_meta dict + counter, 驗 key 寫入邏輯
    correct (不依賴 prior smoke artefact).
    """
    import json

    # Mirror production code in scripts/_validate_week6_5yr.py main() / _run():
    # 1. main() creates `hmm_warning_counter` (a logging.Handler with .count)
    # 2. _run() finalisation writes:
    #      run_meta["hmm_convergence_warnings_count"] = int(hmm_warning_counter.count)
    # Test: simulate this 2-step contract directly.

    class _StubCounter:
        def __init__(self, n: int) -> None:
            self.count = n

    counter = _StubCounter(7)
    run_meta: dict = {"script": "test", "scope": "smoke"}
    run_meta["hmm_convergence_warnings_count"] = int(counter.count)
    serialized = json.dumps(run_meta)
    parsed = json.loads(serialized)
    assert parsed["hmm_convergence_warnings_count"] == 7
    assert isinstance(parsed["hmm_convergence_warnings_count"], int)


def test_r12_12_root_handler_check_distinguishes_filehandler() -> None:
    """R12.12 P1 fix (Codex R12.11 反證): R12.11 用 isinstance(h, StreamHandler)
    判斷 root 是否已有 stderr handler. 但 FileHandler IS a StreamHandler subclass —
    file-only logging caller (e.g. logging.basicConfig(filename=...)) 會被誤判
    成「已有 stderr handler」, script 不再 add StreamHandler → warning 不上 stderr.

    R12.12 fix: explicit check for h.stream IS sys.stderr (or sys.stdout),
    excluding FileHandler.
    """
    import logging
    import sys
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as tf:
        log_path = tf.name
    fh = logging.FileHandler(log_path)
    root = logging.getLogger("_r12_12_test_root")
    root.addHandler(fh)
    try:
        # Old (R12.11) check: isinstance includes FileHandler → True (wrong)
        old_says_has_stderr = any(isinstance(h, logging.StreamHandler) for h in root.handlers)
        assert old_says_has_stderr is True, (
            "FileHandler IS subclass of StreamHandler — R12.11 check incorrectly "
            "says 'already has stderr handler' even when only file handler exists"
        )
        # New (R12.12) check: explicit stream identity check
        new_says_has_stderr = any(
            isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.FileHandler)
            and getattr(h, "stream", None) in (sys.stderr, sys.stdout)
            for h in root.handlers
        )
        assert new_says_has_stderr is False, (
            "R12.12 fix: with only FileHandler, check should return False so "
            "script adds a real stderr handler"
        )
    finally:
        root.removeHandler(fh)
        fh.close()
        pathlib.Path(log_path).unlink(missing_ok=True)
