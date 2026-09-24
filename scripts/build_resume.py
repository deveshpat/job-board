"""Build the resume on GitHub Actions (.github/workflows/resume.yml): the web app can't run LaTeX itself.

  JOBBOARD_DATA_KEY=<base64> python scripts/build_resume.py --state state/

Reads user.enc from --state (a checkout of the `data` branch). If the resume there is LaTeX without a PDF
for its current rev, compiles it into resume_pdf.enc (on a LaTeX error the previous PDF stays and the error
is recorded for the web app to show). Then, if the profile was made from an older resume, gives it a quick
refresh without Kev (facts from the resume; Kev re-reads it properly next time the Mac app runs).
"""
import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import vault  # noqa: E402
from app.db import now  # noqa: E402
from app.jev import Jev  # noqa: E402
from app.profile import build_profile, carry_overrides, refresh_profile  # noqa: E402
from app.resume import CompileError, compile_tex, source_file  # noqa: E402


def read(key: bytes, folder: Path, name: str):
    f = folder / f"{name}.enc"
    return vault.decrypt(key, name, f.read_bytes()) if f.exists() else None


def main(argv=None, compile_fn=compile_tex) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True, type=Path)
    args = ap.parse_args(argv)
    key = vault.unb64(os.environ["JOBBOARD_DATA_KEY"])
    user = read(key, args.state, "user")
    res = (user or {}).get("resume")
    if not res or not res.get("rev"):
        print("No resume in user.enc yet.")
        return 0

    built = read(key, args.state, "resume_pdf") or {}
    if res.get("kind") == "tex" and built.get("rev") != res["rev"]:
        try:
            built = {"rev": res["rev"], "pdf": vault.b64(compile_fn(res["tex"])), "error": None}
            print(f"Compiled resume rev {res['rev']}")
        except CompileError as e:
            built = {**built, "error": {"rev": res["rev"], "message": str(e), "log": e.log[-2000:]}}
            print(f"LaTeX error: {e}")
        (args.state / "resume_pdf.enc").write_bytes(vault.encrypt(key, "resume_pdf", built))

    profile = user.get("profile")
    have_pdf = built.get("rev") == res["rev"] and built.get("pdf")
    if (profile or {}).get("resume_rev") != res["rev"] and (res.get("kind") == "tex" or have_pdf):
        jev_key = os.environ.get("TYPESAFE_API_KEY", "").strip()
        with tempfile.TemporaryDirectory() as tmp:
            path, rev = source_file(res, Path(tmp), vault.unb64(built["pdf"]) if res.get("kind") != "tex" else None)
            try:
                if not jev_key:
                    raise RuntimeError("no model key")
                # the full read (skills, level, roles), your manual edits kept
                new = carry_overrides(profile, build_profile(path, Jev(api_key=jev_key), res.get("filename") or ""))
                new["kev_rev"] = rev
            except Exception as e:                       # facts only; the Mac app finishes the read later
                print(f"Quick profile refresh only ({e})")
                new = refresh_profile(profile, path, rev, res.get("filename") or "")
                new["kev_rev"] = (profile or {}).get("kev_rev")
        new["resume_rev"] = rev
        user["profile"] = new
        user.setdefault("meta", {})["profile"] = now()
        (args.state / "user.enc").write_bytes(vault.encrypt(key, "user", user))
        print("Profile updated from the new resume.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
