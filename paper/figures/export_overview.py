#!/usr/bin/env python3
"""Export the CRAFT overview figure (overview.html -> svg/png/pdf).

Single source of truth is the inline SVG in overview.html; this script
extracts it and renders the delivery formats, so all outputs stay in sync.

Usage:  python3 paper/figures/export_overview.py
Requires: cairosvg
"""
import re
from pathlib import Path

import cairosvg

HERE = Path(__file__).resolve().parent

html = (HERE / "overview.html").read_text(encoding="utf-8")
svg = re.search(r"<svg.*?</svg>", html, re.S).group(0)

# standalone svg keeps the embedded <style>, so it renders identically anywhere
svg = '<?xml version="1.0" encoding="UTF-8"?>\n' + svg
(HERE / "overview.svg").write_text(svg, encoding="utf-8")

cairosvg.svg2png(bytestring=svg.encode(), write_to=str(HERE / "overview.png"),
                 scale=2.0, background_color="white")
cairosvg.svg2pdf(bytestring=svg.encode(), write_to=str(HERE / "overview.pdf"),
                 background_color="white")

for ext in ("svg", "png", "pdf"):
    p = HERE / f"overview.{ext}"
    print(f"wrote {p} ({p.stat().st_size} bytes)")
