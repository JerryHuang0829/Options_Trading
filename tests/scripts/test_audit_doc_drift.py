"""Tests for scripts/audit_doc_drift.py — R11.20 P3 Codex 加碼.

R11.20 教訓: Codex 抓出 audit script 自身有 false positive (Codex-Prompt.md
mutation block 被當 OOS drift) + false negative (Markdown bold + 「R<old> 待
Codex」格式抓不到). 必加 unit tests cover 兩端 (Pattern 11 mutation + Pattern
17 hollow PASS detector — script 自己也是 measured path 必驗).

13 tests:
  1. test_stale_audit_refs_basic_format (R11.20 P2 baseline)
  2. test_stale_audit_refs_markdown_bold_format (R11.20 P2 false negative fix)
  3. test_stale_audit_refs_chinese_colon_variants
  4. test_stale_audit_refs_pending_format (「R<old> 待 Codex」)
  5. test_stale_audit_refs_current_round_exempt (LATEST_AUDIT_ROUND 不算 stale)
  6. test_stale_audit_refs_only_in_handoff_claude_roadmap (其他 file 不 inspect)
  7. test_oos_drift_active_reference (基本 catch)
  8. test_oos_drift_audit_trail_marker_exempt (改名 / R11.X 上下文豁免)
  9. test_oos_drift_codex_prompt_mutation_block_exempt (R11.20 P1 false positive fix)
 10. test_stale_baseline_single_source_exempt (Test baseline 那行豁免)
 11. test_plumbing_confusion_with_disclaimer_exempt
 12. test_plumbing_confusion_caught_when_no_disclaimer
 13. test_run_audit_current_repo_exit_zero (R11.20 P3 driving requirement)
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import audit_doc_drift  # noqa: E402

# A non-script-self path so _walk_repo / per-file path checks behave normally
_HANDOFF_PATH = _REPO_ROOT / "HANDOFF.md"


# ---------------------------------------------------------------------------
# _check_stale_audit_refs (R11.20 P2 — Markdown bold / 中英冒號 / 待 Codex)
# ---------------------------------------------------------------------------


def test_stale_audit_refs_basic_format() -> None:
    """既有 plain 格式仍 catch (R11.20 baseline)."""
    lines = ["下一步：R11.15 Codex Pro audit"]
    hits = audit_doc_drift._check_stale_audit_refs(_HANDOFF_PATH, lines)
    assert len(hits) == 1


def test_stale_audit_refs_markdown_bold_format() -> None:
    """**下一步**：R<old> / **最後一次 Codex audit**: R<old> 必 catch (R11.20 P2)."""
    lines = [
        "**下一步**：R11.15 Codex Pro audit",
        "**最後一次 Codex audit**: R11.14 GO-WITH-CAVEATS",
    ]
    hits = audit_doc_drift._check_stale_audit_refs(_HANDOFF_PATH, lines)
    assert len(hits) == 2


def test_stale_audit_refs_chinese_colon_variants() -> None:
    """中文「：」與英文「:」都 catch."""
    lines = [
        "下一步：R11.15 Codex Pro audit",  # 中文冒號
        "下一步: R11.15 Codex Pro audit",  # 英文冒號
        "最後一次 Codex audit: R11.14 GO",  # 英文冒號
    ]
    hits = audit_doc_drift._check_stale_audit_refs(_HANDOFF_PATH, lines)
    assert len(hits) == 3


def test_stale_audit_refs_pending_format() -> None:
    """「R<old> 待 Codex」/「R<old> 待 audit」格式 catch (R11.20 P2 false negative fix)."""
    lines = [
        "R11.15 待 Codex Pro 等級嚴格審",
        "R11.10 待 audit 完工",
    ]
    hits = audit_doc_drift._check_stale_audit_refs(_HANDOFF_PATH, lines)
    assert len(hits) == 2


def test_stale_audit_refs_current_round_exempt() -> None:
    """LATEST_AUDIT_ROUND 寫的不算 stale (修法時 cross-check 用)."""
    current = audit_doc_drift.LATEST_AUDIT_ROUND
    lines = [
        f"下一步：{current} Codex audit",
        f"**最後一次 Codex audit**: {current} GO-WITH-CAVEATS",
    ]
    hits = audit_doc_drift._check_stale_audit_refs(_HANDOFF_PATH, lines)
    assert len(hits) == 0


def test_stale_audit_refs_only_in_handoff_claude_roadmap() -> None:
    """非 HANDOFF/CLAUDE/roadmap 不 inspect (e.g. README.md)."""
    other_path = _REPO_ROOT / "README.md"
    lines = ["下一步：R11.15 Codex Pro audit"]
    hits = audit_doc_drift._check_stale_audit_refs(other_path, lines)
    assert len(hits) == 0


# ---------------------------------------------------------------------------
# _check_oos_drift (R11.20 P1 — Codex-Prompt mutation block 豁免)
# ---------------------------------------------------------------------------


def test_oos_drift_active_reference() -> None:
    """OOS holdout RMSE active reference 不附 audit-trail marker → catch."""
    p = _REPO_ROOT / "fake.md"
    lines = ["Pro 矩陣 #4 OOS holdout RMSE 計算結果如下"]
    hits = audit_doc_drift._check_oos_drift(p, lines)
    assert len(hits) == 1


def test_oos_drift_audit_trail_marker_exempt() -> None:
    """改名/temporal_drift/R11.15 上下文 → 豁免."""
    p = _REPO_ROOT / "fake.md"
    lines = [
        "Pro 矩陣 #4 OOS holdout RMSE (R11.15 P4 改名 from OOS — 非嚴格 OOS)",
        "改名 OOS holdout RMSE → temporal drift",
        "R11.18 後 OOS holdout RMSE 已棄用",
    ]
    hits = audit_doc_drift._check_oos_drift(p, lines)
    assert len(hits) == 0


def test_oos_drift_codex_prompt_mutation_block_exempt() -> None:
    """R11.20 P1 fix: Codex-Prompt.md mutation/反例注入 block 含 OOS keyword 豁免."""
    p = _REPO_ROOT / "Codex-Prompt.md"
    lines = [
        "B1.2 反注 OOS reference (試在 fake.md 寫 「Pro 矩陣 #4 OOS holdout RMSE」 不附 marker)",
        "mutation test: 反注 OOS holdout RMSE 後 audit script 該 catch",
        "反例: OOS holdout RMSE 在 audit prompt block 內是說明非 active reference",
    ]
    hits = audit_doc_drift._check_oos_drift(p, lines)
    assert len(hits) == 0


# ---------------------------------------------------------------------------
# _check_stale_baselines (Pattern 4 — single source of truth 豁免)
# ---------------------------------------------------------------------------


def test_stale_baseline_single_source_exempt() -> None:
    """`Test baseline` 那行豁免 (single source of truth)."""
    p = _REPO_ROOT / "HANDOFF.md"
    lines = [
        "**Test baseline**: 320 passed, 1 skipped",  # SST 豁免
        "320 passed 在 prior baseline 是 stale",  # 不豁免 (非 SST)
    ]
    hits = audit_doc_drift._check_stale_baselines(p, lines)
    assert len(hits) == 1
    assert "Test baseline" not in hits[0].text


# ---------------------------------------------------------------------------
# _check_plumbing_confusion (Pattern 17 e — spy + 真 e2e 宣稱)
# ---------------------------------------------------------------------------


def test_plumbing_confusion_with_disclaimer_exempt() -> None:
    """test docstring 用 spy 但有 plumbing-only disclaimer → 豁免."""
    p = _REPO_ROOT / "tests" / "backtest" / "test_engine.py"
    lines = [
        "本 test 用 _OpenOnceHoldIC bypass strategy",
        "本 test 是 plumbing proof 不是 real-strategy proof",
        "audit['n_fallback_surface'].sum() > 0  真被觸發 e2e",
    ]
    hits = audit_doc_drift._check_plumbing_confusion(p, lines)
    assert len(hits) == 0


def test_plumbing_confusion_caught_when_no_disclaimer() -> None:
    """spy + 真 e2e 宣稱 + 無 plumbing disclaimer → catch."""
    p = _REPO_ROOT / "tests" / "backtest" / "test_engine.py"
    lines = [
        "用 _OpenOnceHoldIC spy strategy 跑 e2e",
        "assert n_fallback > 0 真被觸發",
    ]
    hits = audit_doc_drift._check_plumbing_confusion(p, lines)
    assert len(hits) >= 1


# ---------------------------------------------------------------------------
# Integration: run full audit on current repo — must exit 0
# ---------------------------------------------------------------------------


def test_run_audit_current_repo_exit_zero() -> None:
    """R11.20 P3 driving requirement: audit script 在當前 repo 必 exit 0.

    若 doc drift 任何一類 active hit → 修法不算成立 (R11.20 verdict).
    """
    report = audit_doc_drift.run_audit()
    assert report.n_drift == 0, (
        f"audit_doc_drift FAIL: drift={report.n_drift}; "
        f"stale_audit={len(report.stale_audit_refs)}, "
        f"oos={len(report.oos_drift)}, "
        f"baselines={len(report.stale_baselines)}, "
        f"plumbing={len(report.plumbing_confusion)}"
    )
