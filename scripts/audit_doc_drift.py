"""Pattern 13 第二類 architectural fix — 自動化 doc drift detection gate (R11.19 加).

Codex R11.19 verdict: 連 9 輪 audit 連 8 次 self-audit skill 升級，pattern 18
(doc drift sweep) 連 2 輪因「我自己違反剛升的紀律」失守。pattern 13 第二類
觸發 — manual grep 紀律已無效，必跳級 architectural fix.

本 script 是 architectural fix 候選 (a) 「自動化 grep 全 repo」具體實作，
比 pre-commit hook (要求 git) / GitHub Actions (要求 push remote) 更輕量；
local repo 即可跑.

檢查項目 (5 類 doc drift, R11.x 累計教訓):

  1. **Stale audit reference** — HANDOFF / CLAUDE / plans / Codex-Prompt 中含
     「下一步：R<old>」「最後一次 Codex audit: R<old>」等過時 audit round 引用
  2. **OOS holdout drift** — R11.15 P4 改名 OOS → temporal drift 後殘留
     「OOS holdout RMSE」/「day_5_4b_oos_rmse.csv」等 active reference
  3. **Stale baseline numbers** — Test baseline single-source-of-truth 之外有
     寫死過時數字 (270/272/275/280/286/304/306/311/319/320/331/338/344)
  4. **Plumbing-vs-real-strategy 混淆** — test docstring 含 「strict e2e: ...
     真被觸發」/「e2e PASS」但實際用 spy/mock/_OpenOnceHoldIC 子類 bypass
  5. **Absolute claim 紅旗** — finding / claim 含「永遠/絕不/必定/不可能/0%」
     絕對句未附反例 stress-test

Exit code: 0 = no drift, 1 = drift found (使 CI / pre-commit / manual run 都
能 catch).

CLI:
  python scripts/audit_doc_drift.py
  python scripts/audit_doc_drift.py --strict  # 把 absolute claim 警告升級為 fail
  python scripts/audit_doc_drift.py --json    # machine-readable output
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

# R11.19 P2 fix: exclude tmp dirs / cache / git
_EXCLUDE_DIRS = {
    ".git",
    "__pycache__",
    ".codex_tmp",
    ".pytest_cache",
    ".pytest_tmp",
    ".venv",
    "venv",
    "env",
    ".vscode",
    ".idea",
    ".ipynb_checkpoints",
    "data",  # cache parquets, not doc
    "outputs",
    "tests/_tmp",
}
_EXCLUDE_GLOB_PATTERNS = ("tmp*",)  # repo-root tmp dirs (Codex env artifact)
_EXCLUDE_FILES = {"audit_doc_drift.py"}  # self-exclude: script 含 keyword 字面是 self-ref 非 drift
_INCLUDE_EXTS = {".py", ".md", ".json", ".yaml", ".yml", ".toml"}

# Latest known audit round — update when bumping audit (R11.X → R11.X+1)
LATEST_AUDIT_ROUND = "R11.21"  # current pending audit; older = stale


@dataclass
class DriftHit:
    file: str
    line: int
    text: str
    category: str

    def fmt(self) -> str:
        return f"  {self.file}:{self.line}  [{self.category}]  {self.text.strip()[:120]}"


@dataclass
class AuditReport:
    stale_audit_refs: list[DriftHit] = field(default_factory=list)
    oos_drift: list[DriftHit] = field(default_factory=list)
    stale_baselines: list[DriftHit] = field(default_factory=list)
    plumbing_confusion: list[DriftHit] = field(default_factory=list)
    absolute_claims: list[DriftHit] = field(default_factory=list)

    @property
    def n_drift(self) -> int:
        return (
            len(self.stale_audit_refs)
            + len(self.oos_drift)
            + len(self.stale_baselines)
            + len(self.plumbing_confusion)
        )

    @property
    def n_warnings(self) -> int:
        return len(self.absolute_claims)


def _walk_repo() -> list[Path]:
    """Yield all repo files under _INCLUDE_EXTS, skipping _EXCLUDE_DIRS."""
    files: list[Path] = []
    for p in _REPO_ROOT.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix not in _INCLUDE_EXTS:
            continue
        # Skip excluded dirs (any path component matches)
        rel = p.relative_to(_REPO_ROOT)
        parts = set(rel.parts)
        if parts & _EXCLUDE_DIRS:
            continue
        # Skip tmp* glob at repo root
        if any(rel.parts[0].startswith(g.rstrip("*")) for g in _EXCLUDE_GLOB_PATTERNS):
            continue
        # Skip self (audit script 含 keyword 字面是 self-ref 非 drift)
        if p.name in _EXCLUDE_FILES:
            continue
        files.append(p)
    return files


def _safe_read_lines(p: Path) -> list[str]:
    try:
        return p.read_text(encoding="utf-8", errors="replace").splitlines()
    except (OSError, UnicodeDecodeError):
        return []


_STALE_AUDIT_REGEX = re.compile(
    # 支援:
    #   下一步：R<old> / 下一步: R<old> / **下一步**：R<old> / **下一步**: R<old>
    #   最後一次 Codex audit: R<old> / 最後一次 audit: R<old> / Markdown bold 變體
    #   R<old> 待 Codex / R<old> 待 audit (前幾輪實際殘留格式)
    r"(?:\*\*)?(?:下一步|最後一次(?:\s+Codex)?\s*audit)(?:\*\*)?\s*[:：]\s*R\d+(?:\.\d+)?"
    r"|R\d+(?:\.\d+)?\s*待\s*(?:Codex|audit)"
)


def _check_stale_audit_refs(p: Path, lines: list[str]) -> list[DriftHit]:
    """Pattern 18(d): HANDOFF / plans / Codex-Prompt 中過時 audit round 引用.

    R11.20 P2 fix (Codex): 改用 regex 支援:
      (a) Markdown bold (`**下一步**：R<old>` / `**最後一次 Codex audit**: R<old>`)
      (b) 中英文冒號 (「：」/「:」)
      (c) 「R<old> 待 Codex」/「R<old> 待 audit」格式 (前幾輪實際殘留)
    現役編號為 LATEST_AUDIT_ROUND；任何 R<old> ≠ LATEST 出現在以上 context = stale.
    """
    hits: list[DriftHit] = []
    if "HANDOFF.md" not in p.name and "CLAUDE.md" not in p.name and "roadmap.md" not in p.name:
        return hits
    for i, line in enumerate(lines, 1):
        match = _STALE_AUDIT_REGEX.search(line)
        if not match:
            continue
        if LATEST_AUDIT_ROUND in line:
            continue  # 引用本輪不算 stale
        # 抽出實際 R-round 編號 (regex 中第一個 R\d+\.\d+ 形式)
        round_match = re.search(r"R(\d+)(?:\.(\d+))?", match.group(0))
        if round_match is None:
            continue
        hits.append(
            DriftHit(
                file=str(p.relative_to(_REPO_ROOT)),
                line=i,
                text=line,
                category="stale_audit_ref",
            )
        )
    return hits


def _check_oos_drift(p: Path, lines: list[str]) -> list[DriftHit]:
    """R11.15 P4: 「OOS holdout RMSE」/「day_5_4b_oos_rmse.csv」active reference.

    Audit-trail acknowledge (含 「改名」/「temporal_drift」/「R11.15 P4」 上下文)
    豁免；其他 = active reference 視為 drift.

    R11.20 P1 fix: Codex-Prompt.md 中 mutation-test / 反例注入 prompt block
    豁免 — script 用「反注 / mutation / 反例」上下文判斷.
    """
    hits: list[DriftHit] = []
    # R11.20 P3 fix: test fixture / unit test 含 OOS keyword 是合法 mutation test
    # 字面 (e.g. tests/scripts/test_audit_doc_drift.py)，不算 active drift.
    if "tests" in p.parts and p.suffix == ".py":
        return hits
    keys = ("OOS holdout RMSE", "day_5_4b_oos_rmse.csv")
    audit_trail_markers = (
        "改名",
        "temporal_drift",
        "R11.15",
        "R11.18",
        "R11.19",
        "R11.20",
        "deferred",
        # R11.20 P1 fix: mutation-test prompt block 豁免
        "反注",
        "mutation",
        "反例",
        "反向注入",
        "Codex 反注",
        "audit prompt",
        "B1.2",  # R11.20 prompt 寫的 mutation block label
    )
    for i, line in enumerate(lines, 1):
        if any(k in line for k in keys) and not any(m in line for m in audit_trail_markers):
            hits.append(
                DriftHit(
                    file=str(p.relative_to(_REPO_ROOT)),
                    line=i,
                    text=line,
                    category="oos_drift",
                )
            )
    return hits


def _check_stale_baselines(p: Path, lines: list[str]) -> list[DriftHit]:
    """Pattern 4: stale baseline 數字 (除 single source of truth 那行)."""
    hits: list[DriftHit] = []
    if p.name not in ("HANDOFF.md", "Codex-Prompt.md"):
        return hits
    stale_nums = (
        "270 passed",
        "272 passed",
        "275 passed",
        "276 passed",
        "280 passed",
        "286 passed",
        "304 passed",
        "306 passed",
        "311 passed",
        "319 passed",
        "320 passed",
        "331 passed",
        "338 passed",
    )
    for i, line in enumerate(lines, 1):
        for num in stale_nums:
            # Single source of truth (Test baseline 那行) 豁免
            if "Test baseline" in line:
                continue
            if num in line:
                hits.append(
                    DriftHit(
                        file=str(p.relative_to(_REPO_ROOT)),
                        line=i,
                        text=line,
                        category="stale_baseline",
                    )
                )
                break
    return hits


def _check_plumbing_confusion(p: Path, lines: list[str]) -> list[DriftHit]:
    """Pattern 17(e): test docstring 用 spy/mock/bypass 但宣稱 「真 e2e PASS」.

    啟發式: 同一 file 出現 「真被觸發 / 真 e2e / e2e PASS」 + 出現
    「_OpenOnceHoldIC / spy / mock / bypass」 但沒寫「plumbing only / plumbing proof」
    → 可能 plumbing-vs-real-strategy 混淆.
    """
    hits: list[DriftHit] = []
    if "test_engine.py" not in p.name and "test_portfolio.py" not in p.name:
        return hits
    text = "\n".join(lines)
    has_real_claim = any(
        k in text for k in ("真被觸發", "真 e2e", "e2e PASS", "real-strategy PASS")
    )
    has_spy = any(k in text for k in ("_OpenOnceHoldIC", "_NeverCloseIC", "Spy", "_spy", "bypass"))
    has_plumbing_disclaimer = any(
        k in text
        for k in ("plumbing proof", "plumbing only", "不是 real-strategy", "non-real-strategy")
    )
    if has_real_claim and has_spy and not has_plumbing_disclaimer:
        for i, line in enumerate(lines, 1):
            if any(k in line for k in ("真被觸發", "真 e2e", "e2e PASS")):
                hits.append(
                    DriftHit(
                        file=str(p.relative_to(_REPO_ROOT)),
                        line=i,
                        text=line,
                        category="plumbing_confusion",
                    )
                )
    return hits


def _check_absolute_claims(p: Path, lines: list[str]) -> list[DriftHit]:
    """Pattern 18(a): 絕對句紅旗 (warn 不 fail by default).

    含 「永遠不」「絕不」「必定」「不可能」「0% / 100%」絕對句未附反例 stress-test
    → warning. 含 「但 / 除非 / except / 反例」上下文 = 已 acknowledge 反例豁免.
    """
    hits: list[DriftHit] = []
    if p.suffix != ".md":
        return hits
    abs_keys = ("永遠不", "絕不", "必定不會", "永遠都", "不可能")
    safety_markers = ("但 ", "除非", "except", "反例", "caveat", "條件", "stress-test")
    for i, line in enumerate(lines, 1):
        if any(k in line for k in abs_keys) and not any(m in line for m in safety_markers):
            hits.append(
                DriftHit(
                    file=str(p.relative_to(_REPO_ROOT)),
                    line=i,
                    text=line,
                    category="absolute_claim",
                )
            )
    return hits


def run_audit() -> AuditReport:
    """Run all 5 doc drift checks across repo. Return aggregated report."""
    report = AuditReport()
    files = _walk_repo()
    for p in files:
        lines = _safe_read_lines(p)
        report.stale_audit_refs.extend(_check_stale_audit_refs(p, lines))
        report.oos_drift.extend(_check_oos_drift(p, lines))
        report.stale_baselines.extend(_check_stale_baselines(p, lines))
        report.plumbing_confusion.extend(_check_plumbing_confusion(p, lines))
        report.absolute_claims.extend(_check_absolute_claims(p, lines))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pattern 13 第二類 doc drift gate")
    parser.add_argument("--strict", action="store_true", help="absolute claims 警告升級為 fail")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    report = run_audit()

    if args.json:
        out = {
            "n_drift": report.n_drift,
            "n_warnings": report.n_warnings,
            "stale_audit_refs": [vars(h) for h in report.stale_audit_refs],
            "oos_drift": [vars(h) for h in report.oos_drift],
            "stale_baselines": [vars(h) for h in report.stale_baselines],
            "plumbing_confusion": [vars(h) for h in report.plumbing_confusion],
            "absolute_claims": [vars(h) for h in report.absolute_claims],
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        print("=" * 70)
        print(f"Doc drift audit — {LATEST_AUDIT_ROUND}")
        print("=" * 70)
        for label, hits in [
            ("Stale audit refs (Pattern 18 d)", report.stale_audit_refs),
            ("OOS → temporal drift (R11.15 P4)", report.oos_drift),
            ("Stale baseline numbers (Pattern 4)", report.stale_baselines),
            ("Plumbing-vs-real-strategy confusion (Pattern 17 e)", report.plumbing_confusion),
        ]:
            print(f"\n[{label}] hits: {len(hits)}")
            for h in hits:
                print(h.fmt())
        print(f"\n[Absolute claims (Pattern 18 a) — warning] hits: {len(report.absolute_claims)}")
        for h in report.absolute_claims:
            print(h.fmt())
        print()
        print(f"Total drift: {report.n_drift}; Warnings: {report.n_warnings}")

    fail = report.n_drift > 0 or (args.strict and report.n_warnings > 0)
    if fail:
        print("\nFAIL: doc drift detected.", file=sys.stderr)
        return 1
    print("\nPASS: no doc drift.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
