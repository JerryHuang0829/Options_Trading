"""Shared pytest fixtures for Options_Trading tests.

Codex R11.4 P1 修法 (廢 R11.1 basetemp / R11.2 sessionfinish / R11.3 tempfile.mkdtemp):
  - pytest 內建 tmp_path_factory cleanup 用 shutil.rmtree 嚴格刪 → Windows AV
    鎖檔 → raise WinError 5
  - R11.3 用 tempfile.mkdtemp 落系統 %TEMP%，Codex 環境系統 %TEMP% 也被 AV 鎖
    → 21 個 test 寫入 fail
  - **R11.4 終極解**：tmp 落 repo 內 `tests/_tmp/<test_name>_<uuid8>/`，避開系統
    %TEMP% AV 掃描 (Codex env 仍可能掃 repo 子目錄，但 user 可加 AV 白名單，
    或至少 cleanup ignore_errors 不會升級成 test failure)
  - tests/_tmp/ 已加進 .gitignore + ruff/mypy exclude
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest

_TMP_BASE = Path(__file__).parent / "_tmp"


@pytest.fixture
def tmp_path(request):
    """Override pytest 內建 tmp_path fixture with repo-internal tests/_tmp/ + ignore_errors.

    R11.4 P1 修法 — solve Codex env Windows AV WinError 5 (R11.3 tempfile.mkdtemp
    落系統 %TEMP% 仍被 AV 鎖造成 21 test fails). 落 repo 內 tests/_tmp/<name>_<uuid>/
    避開系統 %TEMP%；cleanup ignore_errors 確保 AV 鎖不升級為 test failure.
    """
    _TMP_BASE.mkdir(parents=True, exist_ok=True)
    safe_name = request.node.name.replace("[", "_").replace("]", "_").replace("/", "_")[:60]
    td = _TMP_BASE / f"{safe_name}_{uuid.uuid4().hex[:8]}"
    td.mkdir(parents=True, exist_ok=True)
    try:
        yield td
    finally:
        shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
def synthetic_chain():
    """Concrete synthetic TXO chain (3 months, ATM 16800, 4 expiries).

    Returns a pandas.DataFrame with the enriched 24-column schema produced by
    ``src.data.synthetic.generate_chain`` — both raw TAIFEX-aligned columns
    and the enriched ``iv`` / ``delta`` / ``dte`` / ``underlying`` columns
    required by ``src.options.chain`` helpers.
    """
    from src.data.synthetic import SyntheticChainConfig, generate_chain

    return generate_chain(
        SyntheticChainConfig(
            start_date="2026-01-01",
            end_date="2026-03-31",
        )
    )


@pytest.fixture
def mock_broker():
    """Placeholder: mock broker for Phase 2 Shioaji integration tests."""
    pytest.skip("mock_broker fixture pending Phase 2 broker integration")
