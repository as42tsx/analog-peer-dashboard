#!/usr/bin/env python3
"""One-shot extractor: pull embedded JS data constants from source HTML into data/latest.json."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = Path(
    "/home/box/agent-data/agents/f9661bec-0836-4f6e-898a-b26ae004d989/"
    "attachments/3d50b1e48b6856f4579a839fb36c111a5fce0bfa715d39468a37fe5b8697516b.html"
)
OUT = ROOT / "data" / "latest.json"
NODE_HELPER = ROOT / "scripts" / "_extract_node.js"
TZ_SH = timezone(timedelta(hours=8))

NODE_JS = r"""
const fs = require('fs');
const htmlPath = process.argv[2];
const html = fs.readFileSync(htmlPath, 'utf8');
const m = html.match(/<script>([\s\S]*?)<\/script>\s*<\/body>/i);
if (!m) { console.error('no script block found'); process.exit(1); }
let code = m[1];
const stop = code.indexOf('const COLORS');
if (stop < 0) { console.error('COLORS marker not found'); process.exit(1); }
code = code.slice(0, stop);
code = code.replace(/const FOCUS = ALL_COMPANIES\.filter\([^;]+;/, '');
code = code.replace(/function yoy\([^)]*\)\{[^}]*\}/, '');
code = code.replace(/function fmtPct\([^)]*\)\{[^}]*\}/, '');
code = code.replace(/function fmtWan\([^)]*\)\{[^}]*\}/, '');
let data;
try {
  data = Function(code + `
    return {
      ALL_COMPANIES, STAFF, BALANCE, CASH_TABLE, FOCUS_NAMES, DETAIL, OVERSEAS_HTML, COMPANY_ORDER
    };
  `)();
} catch (e) {
  console.error('eval error:', e.message);
  process.exit(1);
}
const titleMatch = html.match(/<title>([^<]+)<\/title>/);
const headerP = html.match(/<header>[\s\S]*?<p>([\s\S]*?)<\/p>/);
const subtitle = headerP
  ? headerP[1].replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&').replace(/\s+/g, ' ').trim()
  : '';
const out = {
  meta: {
    title: titleMatch ? titleMatch[1] : '模拟芯片竞对财务与半年度交流可视化分析',
    updatedAt: new Date().toISOString(),
    periodLabel: '2026H1 vs 2025H1',
    currencyNote: '合并报表口径，单位：万元（现金表为亿元）',
    sourceNote: subtitle || '数据来源：公开半年报 / 交流纪要（非 Wind API）'
  },
  companies: data.ALL_COMPANIES,
  staff: data.STAFF,
  balance: data.BALANCE,
  cash: data.CASH_TABLE,
  focusNames: data.FOCUS_NAMES,
  detail: data.DETAIL,
  overseasHtml: data.OVERSEAS_HTML,
  companyOrder: data.COMPANY_ORDER
};
process.stdout.write(JSON.stringify(out));
"""


def extract(src: Path) -> dict:
    NODE_HELPER.write_text(NODE_JS, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(NODE_HELPER), str(src)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"node extract failed:\n{proc.stderr or proc.stdout}")
    data = json.loads(proc.stdout)
    data["meta"]["updatedAt"] = datetime.now(TZ_SH).isoformat(timespec="seconds")
    # Soften Wind-only wording in sourceNote for the JSON-served dashboard
    sn = data["meta"].get("sourceNote") or ""
    if "Wind" in sn:
        data["meta"]["sourceNote"] = (
            "数据来源：公开半年报（东方财富/巨潮等）及各公司半年度业绩说明会纪要 "
            "| 单位：万元（现金表为亿元）| 定性交流内容存于 JSON，财务字段可脚本刷新"
        )
    return data


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract dashboard data from source HTML")
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    if not args.src.is_file():
        print(f"ERROR: source HTML not found: {args.src}", file=sys.stderr)
        return 1
    data = extract(args.src)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    n = len(data.get("companies") or [])
    dkeys = list((data.get("detail") or {}).keys())
    print(f"Wrote {args.out} ({n} companies, detail keys={dkeys})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
