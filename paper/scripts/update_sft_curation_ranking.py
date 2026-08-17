#!/usr/bin/env python3
"""Apply strictly accepted curation scores to an existing SFT ranking."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from paper.scripts._curation_ranking import ranking_sort_key, zero_relation_rows  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ranking", required=True, type=Path)
    parser.add_argument("--curation", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--stage", required=True)
    args = parser.parse_args()

    ranking = json.loads(args.ranking.read_text(encoding="utf-8"))
    curation = json.loads(args.curation.read_text(encoding="utf-8"))
    selected = {int(row): item for row, item in curation["selected"].items()}
    by_row = {int(item["row"]): item for item in ranking["ranked"]}
    changed_key = f"{args.stage}_changed"
    for item in by_row.values():
        item[changed_key] = False
    for row, result in selected.items():
        score = result["candidate"]
        relation = score["families"]["relation"]
        item = by_row[row]
        item.update(
            relation_total=relation["total"],
            relation_rejected=relation["rejected"],
            relation_rate=(
                relation["rejected"] / relation["total"]
                if relation["total"]
                else None
            ),
            total_rejected=score["rejected"],
            total_traces=score["n_negative_traces"],
            coverage=score["coverage"],
        )
        item[changed_key] = True

    ranked = list(by_row.values())
    ranked.sort(key=ranking_sort_key)
    zero_relation = zero_relation_rows(ranked)
    no_relation = [item["row"] for item in ranked if not item["relation_total"]]
    output = dict(ranking)
    output.update(
        ranked=ranked,
        zero_relation_rows=zero_relation,
        zero_relation_count=len(zero_relation),
        no_relation_negative_count=len(no_relation),
        **{f"{args.stage}_changed": len(selected)},
    )
    args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        f"{args.stage}_changed": len(selected),
        "zero_relation_count": len(zero_relation),
        "no_relation_negative_count": len(no_relation),
    }, indent=2))


if __name__ == "__main__":
    main()
