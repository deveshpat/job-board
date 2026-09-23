"""Run the daily routine now, from a terminal: scrape → skip seen → triage and evaluate the new postings
with the local engine → load the board; the engine is unloaded afterwards. (The app also runs this by
itself every day at settings.daily_at while it is open.)

If the app is open, the run happens inside it (so the UI shows progress and nothing runs twice);
otherwise it runs headless here.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = f"http://127.0.0.1:{os.environ.get('PORT', '8000')}"


def call(path, body=None):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def main():
    print(f"=== daily run {datetime.now():%Y-%m-%d %H:%M}", flush=True)
    try:
        call("/api/status")
        app_open = True
    except (urllib.error.URLError, OSError):
        app_open = False

    if app_open:
        try:
            call("/api/pipeline/run", {})
        except urllib.error.HTTPError as e:
            if e.code != 409:                 # 409 = a run is already going; just wait for it
                raise
        while True:
            state = call("/api/pipeline/status")
            if not state["running"]:
                break
            time.sleep(30)
    else:
        sys.path.insert(0, str(ROOT))
        os.environ["JOBBOARD_HEADLESS"] = "1"   # no scheduler thread in this one-shot process
        import app.main as m                  # loads .env, the database and the engine manager
        try:
            m.pipeline._run_safe(False)
        finally:
            if m.engine:
                m.engine.stop()
        state = m.pipeline.state
    print("\n".join(state["log"][-6:]))
    print(f"=== done ({'in the app' if app_open else 'headless'}) error={state['error']}", flush=True)


if __name__ == "__main__":
    main()
