#!/usr/bin/env python3
"""Embeds output/usage.json into output/dashboard.html (self-contained, no
fetch/CORS issues). Run parse.py first, then this. Generated files stay in
output/ so the tool's root can be zipped/shared without them."""
import json
import os

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(TOOL_DIR, "output")

with open(os.path.join(OUTPUT_DIR, "usage.json"), encoding="utf-8") as f:
    data = json.load(f)

with open(os.path.join(TOOL_DIR, "pricing.json"), encoding="utf-8") as f:
    pricing = json.load(f)["models"]

template = open(os.path.join(TOOL_DIR, "dashboard_template.html"), encoding="utf-8").read()
html = template.replace("__USAGE_DATA__", json.dumps(data))
html = html.replace("__PRICING_DATA__", json.dumps(pricing))

os.makedirs(OUTPUT_DIR, exist_ok=True)
with open(os.path.join(OUTPUT_DIR, "dashboard.html"), "w", encoding="utf-8") as f:
    f.write(html)

print(f"Wrote {OUTPUT_DIR}/dashboard.html")
