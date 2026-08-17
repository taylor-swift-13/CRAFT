"""Shared ranking helpers for SFT rejection-quality curation reports."""

from __future__ import annotations


def ranking_sort_key(item: dict) -> tuple:
    return (
        item["relation_rate"] is None,
        item["relation_rate"] if item["relation_rate"] is not None else 1.0,
        item["coverage"],
        -item["relation_total"],
        item["row"],
    )


def zero_relation_rows(ranked: list[dict]) -> list[int]:
    return [
        item["row"]
        for item in ranked
        if item["relation_total"] and item["relation_rejected"] == 0
    ]
