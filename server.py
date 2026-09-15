#!/usr/bin/env python3
"""Serve Ace's analog peer dashboard on 0.0.0.0:8787."""
from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
DATA = ROOT / "data"


class Handler(SimpleHTTPRequestHandler):
    # directory set via partial below

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self.path = "/index.html"
            return super().do_GET()
        if path == "/data/latest.json" or path.startswith("/data/"):
            # Serve from ROOT/data by temporarily switching directory root
            rel = path.lstrip("/")  # data/latest.json
            file_path = ROOT / rel
            if file_path.is_file() and file_path.resolve().is_relative_to(ROOT.resolve()):
                return self._send_file(file_path)
            self.send_error(404, "File not found")
            return
        return super().do_GET()

    def _send_file(self, file_path: Path) -> None:
        try:
            data = file_path.read_bytes()
        except OSError:
            self.send_error(404, "File not found")
            return
        ctype = "application/json; charset=utf-8" if file_path.suffix == ".json" else "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args) -> None:
        sys_stderr = __import__("sys").stderr
        print("[%s] %s" % (self.log_date_time_string(), fmt % args), file=sys_stderr)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args()
    if not (PUBLIC / "index.html").is_file():
        raise SystemExit(f"missing {PUBLIC / 'index.html'}")
    if not (DATA / "latest.json").is_file():
        raise SystemExit(f"missing {DATA / 'latest.json'} — run scripts/extract_from_html.py")

    handler = partial(Handler, directory=str(PUBLIC))
    httpd = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Serving {PUBLIC} + {DATA} on http://{args.host}:{args.port}/", flush=True)
    print(f"  local: http://127.0.0.1:{args.port}/", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
