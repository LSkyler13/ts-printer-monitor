#!/usr/bin/env python3
import argparse, subprocess, sys, os, shutil, json

def run(cmd):
    print(">", " ".join(cmd))
    subprocess.run(cmd, check=True)

def pick_json_path(preferred=None):
    # if a specific path is provided and exists, use it
    if preferred and os.path.exists(preferred):
        return preferred
    # otherwise try the common names in order
    for candidate in ("printers.json", "app-printers.json", "app_printers.json"):
        if os.path.exists(candidate):
            return candidate
    # as a last resort, return the preferred even if missing (renderer will error)
    return preferred or "printers.json"

def main():
    p = argparse.ArgumentParser(description="Probe -> scrape -> render -> open.")
    p.add_argument("-c", "--config", default="config.json")
    p.add_argument("-s", "--connectivity", default="connectivity.json")
    p.add_argument("--max-workers", default="2")
    p.add_argument("--python", default=sys.executable)

    # HTML output params
    p.add_argument("-o", "--html-out", default="app-report.html")
    p.add_argument("--banner", default="vcutsbanner.png")
    p.add_argument("--title", default="Printer Status")
    p.add_argument("--no-open", action="store_true")
    args = p.parse_args()

    # 1) connectivity -> connectivity.json
    run([args.python, "connectivity.py", "-c", args.config, "-o", args.connectivity])

    # 2) scrape -> (scraper decides the JSON filename; no -o supported)
    run([args.python, "app.py", "-c", args.config, "-s", args.connectivity, "--max-workers", str(args.max_workers)])

    # 3) find the produced JSON (printers.json vs app-printers.json)
    json_in = pick_json_path()

    if not os.path.exists(json_in):
        raise SystemExit(f"Expected printers JSON not found (looked for printers.json/app-printers.json). "
                         f"Check what app.py wrote, then re-run with: app-html.py -i <that-file>")

    # 4) render HTML
    run([args.python, "app-html.py", "-i", json_in, "-o", args.html_out, "--banner", args.banner, "--title", args.title])

    # optional cleanup
    try: os.remove("page.html")
    except FileNotFoundError: pass

    # 5) open report
    if not args.no_open:
        opener = "open" if shutil.which("open") else ("xdg-open" if shutil.which("xdg-open") else None)
        if opener: run([opener, args.html_out])
        else: print(f"Open the report at: {os.path.abspath(args.html_out)}")

if __name__ == "__main__":
    main()
# python3 run_all.py