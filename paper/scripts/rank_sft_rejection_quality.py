#!/usr/bin/env python3
"""Rank SFT answers by negative-family rejection quality."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from paper.scripts._curation_ranking import ranking_sort_key, zero_relation_rows  # noqa: E402

DEFAULT_LEDGER = ROOT / "paper" / "artifacts" / "sft_negative_rejection.jsonl"
DEFAULT_OUTPUT = ROOT / "paper" / "artifacts" / "sft_rejection_ranking.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    by_row = {
        item["row"]: item
        for line in args.ledger.read_text(encoding="utf-8").splitlines()
        if line.strip()
        for item in [json.loads(line)]
    }
    ranked = []
    unscorable = []
    no_negatives = []
    for row, item in sorted(by_row.items()):
        if not item.get("scorable"):
            unscorable.append(row)
            continue
        if not item.get("n_negative_traces"):
            no_negatives.append(row)
            continue
        relation = item["families"]["relation"]
        relation_rate = (
            relation["rejected"] / relation["total"] if relation["total"] else None
        )
        ranked.append(
            {
                "row": row,
                "relation_total": relation["total"],
                "relation_rejected": relation["rejected"],
                "relation_rate": relation_rate,
                "total_rejected": item["rejected"],
                "total_traces": item["n_negative_traces"],
                "coverage": item["coverage"],
            }
        )
    ranked.sort(key=ranking_sort_key)
    zero_relation = zero_relation_rows(ranked)
    report = {
        "schema_version": 1,
        "rows": len(by_row),
        "priority_zero_relation_rejection": zero_relation,
        "priority_zero_relation_count": len(zero_relation),
        "no_negative_rows": no_negatives,
        "unscorable_rows": unscorable,
        "ranked": ranked,
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "zero_relation": len(zero_relation),
                "no_negatives": len(no_negatives),
                "unscorable": len(unscorable),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
