#!/usr/bin/env python3
from pathlib import Path
import re
import shutil

root = Path(__file__).resolve().parents[1]
html_path = root / "public" / "index.html"
data_path = root / "data" / "latest.json"
html = html_path.read_text(encoding="utf-8")
data = data_path.read_text(encoding="utf-8")
html = html.replace('fetch("/data/latest.json"', 'fetch("data/latest.json"')
if "window.__DASH_DATA__" in html:
    html = re.sub(
        r"<script>window\.__DASH_DATA__ = \{.*?\};</script>\n",
        f"<script>window.__DASH_DATA__ = {data};</script>\n",
        html,
        count=1,
        flags=re.S,
    )
else:
    html = html.replace("</head>", f"<script>window.__DASH_DATA__ = {data};</script>\n</head>", 1)
html_path.write_text(html, encoding="utf-8")
docs = root / "docs"
docs.mkdir(exist_ok=True)
(docs / "data").mkdir(exist_ok=True)
(docs / "index.html").write_text(html, encoding="utf-8")
shutil.copy2(data_path, docs / "data" / "latest.json")
print("docs refreshed", (docs / "index.html").stat().st_size)
