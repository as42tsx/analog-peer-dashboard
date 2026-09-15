# 模拟芯片竞对看板

Ace 用的 2026H1 财务与半年度交流可视化。静态页由 GitHub Pages 托管；数据在 `docs/data/latest.json`。

本地：`python3 server.py` → http://127.0.0.1:8787/

更新财务：`python3 scripts/update_finance.py && python3 scripts/build_docs.py` 后 push。
