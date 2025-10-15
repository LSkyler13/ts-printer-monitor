#!/usr/bin/env python3
# app.py — run one-shot scan and write JSON (optionally also HTML)
import argparse, os, sys, json
from core import load_config, load_printers_from_config, collect_all
from app_html import build_html as build_report  # small rename below

def save_json(path: str, obj):
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)

def main() -> int:
    ap = argparse.ArgumentParser(description="Scan printers and write outputs.")
    ap.add_argument("-c", "--config", required=True)
    ap.add_argument("-o", "--output", default="app-printers.json")
    ap.add_argument("--max-workers", type=int, default=8)
    ap.add_argument("--html", help="Optional HTML output path")
    args = ap.parse_args()

    cfg = load_config(args.config)
    printers = load_printers_from_config(cfg)
    rows = collect_all(printers, cfg.get("http", {}), max_workers=args.max_workers)
    save_json(args.output, rows)

    if args.html:
        html = build_report(rows, banner_src="vcutsbanner.png", title="Printer Status")
        with open(args.html, "w", encoding="utf-8") as f:
            f.write(html)

    print(f"Wrote {len(rows)} printers to {os.path.abspath(args.output)}")
    if args.html:
        print(f"Wrote HTML to {os.path.abspath(args.html)}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())