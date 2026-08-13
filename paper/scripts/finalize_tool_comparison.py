"""Render final tool-comparison rows from the strict audit artifact."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "paper/artifacts/tool_comparison_final_audit.json"


def cell(n: int, denominator: int) -> str:
    return f"{n} ({100*n/denominator:.2f}\\%)"


def row(label: str, data: dict) -> str:
    b = data["by_suite"]
    return (
        f"{label}\n"
        f"  & {cell(b['linear'],316)} & {cell(b['NLA_lipus'],50)} & "
        f"{cell(b['Loopy'],466)}\n"
        f"  & {cell(data['verified'],832)} & "
        f"{data['mean_total_tokens']:,.2f} & "
        f"{data['mean_generation_seconds']:.2f}\\,s \\\\"
    )


def main() -> None:
    audit = json.loads(AUDIT.read_text())
    main = audit["main"]
    print("MAIN_COMBINE10")
    print(row("\\sam{} (combine@10)", main["combine10"]))
    print("MAIN_LOOPY")
    print(row("\\textsc{Loopy}", main["loopy"]))
    print("APPENDIX_LOOPY_MEDIUM")
    print(row("\\textsc{Loopy}", audit["appendix"]["loopy_reasoning_medium"]))


if __name__ == "__main__":
    main()
