
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
