#!/usr/bin/env python3
"""
Refresh finance fields in data/latest.json from public A-share sources
(Eastmoney / sina-style public APIs). Does NOT use Wind.

Updates (when available): companies rev/gm/np/rd/sales for latest vs prior semi;
staff rd headcount / pct if disclosed. Preserves detail / overseasHtml.

Units: 万元 for income-statement style fields (as in original dashboard).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "latest.json"
LOG = ROOT / "data" / "update_log.json"
TZ_SH = timezone(timedelta(hours=8))

UA = (
    "Mozilla/5.0 (compatible; AnalogPeerDashboard/1.0; +local-research; "
    "not-a-wind-client)"
)
TIMEOUT = 20

# Report type: 一季报=1 中报=2 三季报=3 年报=4
# We prefer 中报 (semi) for H1 comparisons.


def now_iso() -> str:
    return datetime.now(TZ_SH).isoformat(timespec="seconds")


def http_get(url: str, headers: dict | None = None) -> bytes:
    h = {"User-Agent": UA, "Accept": "*/*", "Referer": "https://data.eastmoney.com/"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


def http_get_json(url: str) -> Any:
    raw = http_get(url)
    text = raw.decode("utf-8", errors="replace")
    # Eastmoney sometimes wraps JSONP
    if text.startswith("jQuery") or text.startswith("callback"):
        text = text[text.find("(") + 1 : text.rfind(")")]
    return json.loads(text)


def code_to_secid(code: str) -> str | None:
    """688484.SH -> 1.688484 ; 300661.SZ -> 0.300661"""
    code = (code or "").strip().upper()
    m = re.match(r"^(\d{6})\.(SH|SZ)$", code)
    if not m:
        return None
    num, mkt = m.group(1), m.group(2)
    return f"{'1' if mkt == 'SH' else '0'}.{num}"


def code_to_em_code(code: str) -> str | None:
    """688484.SH -> SH688484"""
    code = (code or "").strip().upper()
    m = re.match(r"^(\d{6})\.(SH|SZ)$", code)
    if not m:
        return None
    return f"{m.group(2)}{m.group(1)}"


def yuan_to_wan(v: Any) -> float | None:
    if v is None or v == "" or v == "-":
        return None
    try:
        return float(v) / 10000.0
    except (TypeError, ValueError):
        return None


def pct_as_is(v: Any) -> float | None:
    if v is None or v == "" or v == "-":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_eastmoney_income(secid: str) -> list[dict]:
    """
    Eastmoney RPT_LICO_FN_INCOME / datacenter report.
    Returns list of report rows (newest first typically).
    Fields commonly include TOTALOPERATEREVE, PARENTNETPROFIT, XSMLL (gross margin %),
    RESEARCHEXPENSE / RESEARCH_EXPENSE / RD_EXPENSE variants, SALEEXPENSE.
    """
    # Primary: datacenter report API (public)
    params = {
        "reportName": "RPT_DMSK_FN_INCOME",
        "columns": "ALL",
        "filter": f'(SECURITY_CODE="{secid.split(".", 1)[1]}")',
        "pageNumber": "1",
        "pageSize": "20",
        "sortTypes": "-1",
        "sortColumns": "REPORT_DATE",
        "source": "WEB",
        "client": "WEB",
    }
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get?" + urllib.parse.urlencode(
        params
    )
    try:
        data = http_get_json(url)
    except Exception as e:
        raise RuntimeError(f"eastmoney income request failed: {e}") from e
    result = (data or {}).get("result") or {}
    return result.get("data") or []


def fetch_eastmoney_balance(secid: str) -> list[dict]:
    params = {
        "reportName": "RPT_DMSK_FN_BALANCE",
        "columns": "ALL",
        "filter": f'(SECURITY_CODE="{secid.split(".", 1)[1]}")',
        "pageNumber": "1",
        "pageSize": "20",
        "sortTypes": "-1",
        "sortColumns": "REPORT_DATE",
        "source": "WEB",
        "client": "WEB",
    }
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get?" + urllib.parse.urlencode(
        params
    )
    try:
        data = http_get_json(url)
    except Exception as e:
        raise RuntimeError(f"eastmoney balance request failed: {e}") from e
    result = (data or {}).get("result") or {}
    return result.get("data") or []


def fetch_eastmoney_cashflow(secid: str) -> list[dict]:
    params = {
        "reportName": "RPT_DMSK_FN_CASHFLOW",
        "columns": "ALL",
        "filter": f'(SECURITY_CODE="{secid.split(".", 1)[1]}")',
        "pageNumber": "1",
        "pageSize": "20",
        "sortTypes": "-1",
        "sortColumns": "REPORT_DATE",
        "source": "WEB",
        "client": "WEB",
    }
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get?" + urllib.parse.urlencode(
        params
    )
    try:
        data = http_get_json(url)
    except Exception as e:
        raise RuntimeError(f"eastmoney cashflow request failed: {e}") from e
    result = (data or {}).get("result") or {}
    return result.get("data") or []


def is_semi_row(row: dict) -> bool:
    """True if mid-year report (REPORT_TYPE / REPORT_DATE_TYPE / date ends with -06-30)."""
    for k in ("REPORT_TYPE", "REPORT_DATE_TYPE", "REPORTTYPE", "DATE_TYPE_CODE"):
        v = str(row.get(k) or "")
        if "中报" in v or v in ("2", "002", "中期"):
            return True
    rd = str(row.get("REPORT_DATE") or row.get("REPORTDATE") or "")
    if "-06-30" in rd or rd.endswith("0630"):
        return True
    return False


def row_year(row: dict) -> int | None:
    rd = str(row.get("REPORT_DATE") or row.get("REPORTDATE") or "")
    m = re.search(r"(20\d{2})", rd)
    return int(m.group(1)) if m else None


def pick_semis(rows: list[dict], prefer_year: int | None = None) -> tuple[dict | None, dict | None]:
    """Return (latest_semi, prior_semi) preferring prefer_year then previous."""
    semis = [r for r in rows if is_semi_row(r)]
    semis.sort(key=lambda r: str(r.get("REPORT_DATE") or ""), reverse=True)
    if not semis:
        return None, None
    if prefer_year:
        latest = next((r for r in semis if row_year(r) == prefer_year), None)
        prior = next((r for r in semis if row_year(r) == prefer_year - 1), None)
        if latest:
            return latest, prior
    latest = semis[0]
    y = row_year(latest)
    prior = next((r for r in semis[1:] if y and row_year(r) == y - 1), semis[1] if len(semis) > 1 else None)
    return latest, prior


def extract_income_fields(row: dict | None) -> dict[str, float | None]:
    if not row:
        return {"rev": None, "np": None, "gm": None, "rd": None, "sales": None}
    # Revenue
    rev = None
    for k in ("TOTAL_OPERATE_INCOME", "TOTALOPERATEREVE", "OPERATE_INCOME", "OPERATEINCOME"):
        if row.get(k) is not None:
            rev = yuan_to_wan(row[k])
            break
    # Net profit parent
    np_ = None
    for k in ("PARENT_NETPROFIT", "PARENTNETPROFIT", "NETPROFIT"):
        if row.get(k) is not None:
            np_ = yuan_to_wan(row[k])
            break
    # Gross margin % — Eastmoney often has SALE_GPMARGIN / GROSSPROFITRATIO / XSMLL
    gm = None
    for k in ("SALE_GPMARGIN", "GROSSPROFITRATIO", "XSMLL", "GROSS_PROFIT_RATIO"):
        if row.get(k) is not None:
            gm = pct_as_is(row[k])
            break
    if gm is None:
        # try compute from revenue - cost
        cost = None
        for k in ("TOTAL_OPERATE_COST", "OPERATE_COST", "OPERATECOST"):
            if row.get(k) is not None:
                cost = float(row[k])
                break
        # Better: OPERATE_INCOME and OPERATE_COST for COGS-style; or DEDUCT fields.
        # Many EM rows include OPERATE_INCOME and a cost of sales field.
        cogs = None
        for k in ("OPERATE_COST", "OPERATECOST"):
            if row.get(k) is not None:
                cogs = float(row[k])
                break
        rev_y = None
        for k in ("TOTAL_OPERATE_INCOME", "TOTALOPERATEREVE", "OPERATE_INCOME"):
            if row.get(k) is not None:
                rev_y = float(row[k])
                break
        if rev_y and cogs is not None and rev_y != 0:
            gm = (rev_y - cogs) / rev_y * 100.0
    # R&D
    rd = None
    for k in ("RESEARCH_EXPENSE", "RESEARCHEXPENSE", "RD_EXPENSE", "DEVELOP_EXPENSE"):
        if row.get(k) is not None:
            rd = yuan_to_wan(row[k])
            break
    # Sales expense
    sales = None
    for k in ("SALE_EXPENSE", "SALEEXPENSE", "SELL_EXPENSE"):
        if row.get(k) is not None:
            sales = yuan_to_wan(row[k])
            break
    return {"rev": rev, "np": np_, "gm": gm, "rd": rd, "sales": sales}


def extract_balance_fields(row: dict | None) -> dict[str, float | None]:
    if not row:
        return {"inv": None, "imp": None, "cash": None, "fin": None}
    inv = None
    for k in ("INVENTORY", "INVENTORY_NET", "INVENTORYNETAMOUNT"):
        if row.get(k) is not None:
            inv = yuan_to_wan(row[k])
            break
    # Impairment is usually P&L; leave None here
    cash = None
    for k in ("MONETARYFUNDS", "MONETARY_FUNDS", "CASH_CASH_EQU"):
        if row.get(k) is not None:
            # cash table uses 亿元
            try:
                cash = float(row[k]) / 1e8
            except (TypeError, ValueError):
                cash = None
            break
    fin = None
    for k in ("TRADE_FINASSET", "TRADE_FINASSET_NOTFVTPL", "OTHER_EQUITY_INVEST"):
        # trading financial assets — approximate "fin" bucket; may not match original definition
        if row.get(k) is not None:
            try:
                fin = float(row[k]) / 1e8
            except (TypeError, ValueError):
                fin = None
            break
    return {"inv": inv, "imp": None, "cash": cash, "fin": fin}


def extract_cfo(row: dict | None) -> float | None:
    if not row:
        return None
    for k in ("NETCASH_OPERATE", "NETCASHOPERATE", "OPERATE_CASH_FLOW_NET"):
        if row.get(k) is not None:
            try:
                return float(row[k]) / 1e8  # 亿元
            except (TypeError, ValueError):
                return None
    return None


def round_or_none(v: float | None, nd: int = 1) -> float | None:
    if v is None:
        return None
    return round(v, nd)


def update_company(co: dict, latest: dict, prior: dict, log_entries: list) -> None:
    name = co.get("name")
    mapping = [
        ("rev26", latest.get("rev"), 0),
        ("rev25", prior.get("rev"), 0),
        ("np26", latest.get("np"), 0),
        ("np25", prior.get("np"), 0),
        ("gm26", latest.get("gm"), 1),
        ("gm25", prior.get("gm"), 1),
        ("rd26", latest.get("rd"), 0),
        ("rd25", prior.get("rd"), 0),
        ("sales26", latest.get("sales"), 0),
        # sales25 not always in ALL_COMPANIES
    ]
    for field, val, nd in mapping:
        if val is None:
            log_entries.append(
                {
                    "company": name,
                    "field": field,
                    "status": "skipped",
                    "reason": "not available from public API / missing report row",
                }
            )
            continue
        old = co.get(field)
        new = round(val, nd) if nd else round(val)
        # keep integer-looking for wan amounts
        if nd == 0:
            new = int(round(val))
        co[field] = new
        log_entries.append(
            {
                "company": name,
                "field": field,
                "status": "updated",
                "old": old,
                "new": new,
            }
        )


def update_balance_row(bal: dict | None, latest_inv, prior_inv, log_entries, name):
    if not bal:
        log_entries.append(
            {"company": name, "field": "balance", "status": "skipped", "reason": "no balance row in JSON"}
        )
        return
    for field, val in (("inv26", latest_inv), ("inv25", prior_inv)):
        if val is None:
            log_entries.append(
                {"company": name, "field": field, "status": "skipped", "reason": "inventory not in public row"}
            )
            continue
        old = bal.get(field)
        new = int(round(val))
        bal[field] = new
        log_entries.append({"company": name, "field": field, "status": "updated", "old": old, "new": new})


def update_cash_row(cash_rows: list, name: str, cash_yi, cfo_yi, log_entries):
    row = next((c for c in cash_rows if c.get("name") == name), None)
    if not row:
        log_entries.append(
            {"company": name, "field": "cash", "status": "skipped", "reason": "not in CASH_TABLE"}
        )
        return
    if cash_yi is not None:
        old = row.get("cash")
        new = round(cash_yi, 1)
        row["cash"] = new
        # recompute avail if fin known
        if row.get("fin") is not None:
            row["avail"] = round(new + float(row["fin"]), 1)
        elif row.get("avail") is not None and old is not None:
            # shift avail by delta cash
            try:
                row["avail"] = round(float(row["avail"]) + (new - float(old)), 1)
            except (TypeError, ValueError):
                pass
        log_entries.append({"company": name, "field": "cash", "status": "updated", "old": old, "new": new})
    else:
        log_entries.append(
            {"company": name, "field": "cash", "status": "skipped", "reason": "cash not fetched"}
        )
    if cfo_yi is not None:
        old = row.get("cfo")
        new = round(cfo_yi, 1)
        row["cfo"] = new
        log_entries.append({"company": name, "field": "cfo", "status": "updated", "old": old, "new": new})
    else:
        log_entries.append(
            {"company": name, "field": "cfo", "status": "skipped", "reason": "cfo not fetched"}
        )


def sync_detail_fin(detail: dict, companies: list[dict], log_entries: list) -> None:
    """Mirror company finance into detail[*].fin when present; never touch qualitative fields."""
    by_name = {c["name"]: c for c in companies}
    for name, d in detail.items():
        if not isinstance(d, dict) or d.get("noFin") or "fin" not in d:
            continue
        co = by_name.get(name)
        if not co:
            continue
        fin = d["fin"]
        for src, dst in (
            ("rev26", "rev26"),
            ("rev25", "rev25"),
            ("gm26", "gm26"),
            ("gm25", "gm25"),
            ("np26", "np26"),
            ("np25", "np25"),
            ("rd26", "rd26"),
            ("rd25", "rd25"),
            ("sales26", "sales26"),
        ):
            if co.get(src) is not None and dst in fin:
                old = fin.get(dst)
                fin[dst] = co[src]
                if old != co[src]:
                    log_entries.append(
                        {
                            "company": name,
                            "field": f"detail.fin.{dst}",
                            "status": "updated",
                            "old": old,
                            "new": co[src],
                        }
                    )


def process_company(
    co: dict,
    balance_by_name: dict,
    cash_rows: list,
    prefer_year: int,
    log_entries: list,
    dry_run: bool,
) -> None:
    name = co.get("name", "?")
    code = co.get("code", "")
    secid = code_to_secid(code)
    if not secid:
        log_entries.append(
            {
                "company": name,
                "field": "*",
                "status": "skipped",
                "reason": f"unrecognized code {code!r}",
            }
        )
        return
    try:
        income_rows = fetch_eastmoney_income(secid)
        time.sleep(0.35)
        bal_rows = fetch_eastmoney_balance(secid)
        time.sleep(0.35)
        cf_rows = fetch_eastmoney_cashflow(secid)
        time.sleep(0.35)
    except Exception as e:
        log_entries.append(
            {
                "company": name,
                "field": "*",
                "status": "error",
                "reason": str(e),
            }
        )
        return

    if not income_rows:
        log_entries.append(
            {
                "company": name,
                "field": "*",
                "status": "skipped",
                "reason": "empty income rows from Eastmoney (report may not be published yet)",
            }
        )
        return

    latest_row, prior_row = pick_semis(income_rows, prefer_year=prefer_year)
    latest = extract_income_fields(latest_row)
    prior = extract_income_fields(prior_row)
    log_entries.append(
        {
            "company": name,
            "field": "_report",
            "status": "info",
            "latest_date": (latest_row or {}).get("REPORT_DATE"),
            "prior_date": (prior_row or {}).get("REPORT_DATE"),
            "income_row_count": len(income_rows),
        }
    )

    if not dry_run:
        update_company(co, latest, prior, log_entries)
    else:
        for field, val in {
            "rev26": latest["rev"],
            "rev25": prior["rev"],
            "np26": latest["np"],
            "gm26": latest["gm"],
            "rd26": latest["rd"],
            "sales26": latest["sales"],
        }.items():
            log_entries.append(
                {
                    "company": name,
                    "field": field,
                    "status": "dry_run",
                    "new": val,
                    "old": co.get(field),
                }
            )

    # balance inventory
    b_latest, b_prior = pick_semis(bal_rows, prefer_year=prefer_year)
    inv_l = extract_balance_fields(b_latest)["inv"]
    inv_p = extract_balance_fields(b_prior)["inv"]
    cash_yi = extract_balance_fields(b_latest)["cash"]
    cf_latest, _ = pick_semis(cf_rows, prefer_year=prefer_year)
    cfo_yi = extract_cfo(cf_latest)

    if not dry_run:
        update_balance_row(balance_by_name.get(name), inv_l, inv_p, log_entries, name)
        update_cash_row(cash_rows, name, cash_yi, cfo_yi, log_entries)
    else:
        log_entries.append(
            {
                "company": name,
                "field": "inv26/cash/cfo",
                "status": "dry_run",
                "inv26": inv_l,
                "cash": cash_yi,
                "cfo": cfo_yi,
            }
        )


def main() -> int:
    ap = argparse.ArgumentParser(description="Update finance fields from public Eastmoney APIs")
    ap.add_argument("--data", type=Path, default=DATA)
    ap.add_argument("--log", type=Path, default=LOG)
    ap.add_argument("--prefer-year", type=int, default=2026, help="Prefer this year's mid-year report")
    ap.add_argument("--only", type=str, default="", help="Comma-separated company names to update")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="Max companies to attempt (0=all)")
    args = ap.parse_args()

    if not args.data.is_file():
        print(f"ERROR: {args.data} not found — run extract_from_html.py first", file=sys.stderr)
        return 1

    payload = json.loads(args.data.read_text(encoding="utf-8"))
    # Preserve qualitative blobs by reference (we never overwrite these keys wholesale)
    detail = payload.get("detail")
    overseas = payload.get("overseasHtml")

    companies = payload.get("companies") or []
    balance = payload.get("balance") or []
    cash = payload.get("cash") or []
    balance_by_name = {b["name"]: b for b in balance}

    only = {x.strip() for x in args.only.split(",") if x.strip()}
    targets = [c for c in companies if not only or c.get("name") in only]
    if args.limit:
        targets = targets[: args.limit]

    log_entries: list[dict] = []
    started = now_iso()
    print(f"Updating {len(targets)} companies (prefer_year={args.prefer_year}, dry_run={args.dry_run})")

    for co in targets:
        print(f"  → {co.get('name')} ({co.get('code')})")
        try:
            process_company(co, balance_by_name, cash, args.prefer_year, log_entries, args.dry_run)
        except Exception as e:
            log_entries.append(
                {"company": co.get("name"), "field": "*", "status": "error", "reason": str(e)}
            )
            print(f"    ERROR: {e}")

    if not args.dry_run:
        sync_detail_fin(detail or {}, companies, log_entries)
        # Ensure qualitative content untouched
        payload["detail"] = detail
        payload["overseasHtml"] = overseas
        meta = payload.setdefault("meta", {})
        meta["updatedAt"] = now_iso()
        meta["sourceNote"] = (
            "数据来源：公开半年报（东方财富 datacenter 等）及各公司半年度业绩说明会纪要 "
            "| 单位：万元（现金表为亿元）| 定性交流内容存于 JSON，财务字段可脚本刷新"
        )
        args.data.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {args.data}")

    summary = {
        "startedAt": started,
        "finishedAt": now_iso(),
        "preferYear": args.prefer_year,
        "dryRun": args.dry_run,
        "targets": [c.get("name") for c in targets],
        "counts": {
            "updated": sum(1 for e in log_entries if e.get("status") == "updated"),
            "skipped": sum(1 for e in log_entries if e.get("status") == "skipped"),
            "error": sum(1 for e in log_entries if e.get("status") == "error"),
            "info": sum(1 for e in log_entries if e.get("status") == "info"),
            "dry_run": sum(1 for e in log_entries if e.get("status") == "dry_run"),
        },
        "entries": log_entries,
        "notes": [
            "Staff (研发人员) is rarely in the income/balance APIs used here — left unchanged unless a future source is added.",
            "impairment (imp26/imp25) not reliably mapped from public balance rows — left unchanged.",
            "Overseas / 帝奥微 qualitative DETAIL and overseasHtml are never overwritten by this script.",
            "No Wind API is used.",
        ],
    }
    args.log.parent.mkdir(parents=True, exist_ok=True)
    args.log.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.log}")
    print("counts:", summary["counts"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
