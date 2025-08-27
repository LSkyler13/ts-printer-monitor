# server_min_venv.py — prefers .venv python if present and runs the 3-step pipeline
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import subprocess, sys, os, datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=BASE_DIR, static_url_path="")
CORS(app)

def pick_python():
    """Prefer the project's venv Python if it exists; otherwise fall back to current."""
    candidates = [
        os.path.join(BASE_DIR, ".venv", "bin", "python"),
        os.path.join(BASE_DIR, ".venv", "bin", "python3"),
        os.path.join(BASE_DIR, ".venv", "Scripts", "python.exe"),  # Windows
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return sys.executable

PY = pick_python()

def run(cmd_list):
    """Run a command list with env pointing at the venv."""
    env = os.environ.copy()
    venv_bin = os.path.dirname(PY)
    if venv_bin not in env.get("PATH", ""):
        env["PATH"] = venv_bin + os.pathsep + env.get("PATH", "")
    return subprocess.run(cmd_list, cwd=BASE_DIR, capture_output=True, text=True, timeout=600, env=env)

@app.post("/run-check")
def run_check():
    """
    Pipeline:
      1) python connectivity.py
      2) python app.py -c config.json -s connectivity.json --max-workers 7 -o app-printers.json
      3) python app-html.py -i app-printers.json -o app-report.html
    """
    try:
        p = lambda *parts: os.path.join(BASE_DIR, *parts)  # absolute paths

        cmds = [
            # 1) connectivity
            [PY, p("connectivity.py")],

            # 2) main scrape with required args
            [PY, p("app.py"),
             "-c", p("config.json"),
             "-s", p("connectivity.json"),
             "--max-workers", "7",
             "-o", p("app-printers.json")],

            # 3) render HTML report
            [PY, p("app-html.py"),
             "-i", p("app-printers.json"),
             "-o", p("app-report.html")],
        ]

        logs, ok = [], True
        for cmd in cmds:
            proc = run(cmd)
            logs.append({
                "cmd": " ".join(cmd),
                "rc": proc.returncode,
                "stdout": proc.stdout[-4000:],   # tail to keep response reasonable
                "stderr": proc.stderr[-4000:],
            })
            if proc.returncode != 0:
                ok = False
                break

        return jsonify({
            "ok": ok,
            "ran_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "logs": logs
        }), (200 if ok else 500)

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.get("/app-report.html")
def report():
    return send_from_directory(BASE_DIR, "app-report.html", mimetype="text/html", max_age=0)

@app.get("/")
def root():
    # Serve the debug dashboard if present; otherwise fall back to the normal one.
    for name in ("printer-dashboard-debug.html", "printer-dashboard.html"):
        path = os.path.join(BASE_DIR, name)
        if os.path.exists(path):
            return send_from_directory(BASE_DIR, name, mimetype="text/html", max_age=0)
    return (
        "<h1>Dashboard not found</h1>"
        "<p>Place <code>printer-dashboard-debug.html</code> or "
        "<code>printer-dashboard.html</code> in this folder.</p>",
        404,
    )

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
