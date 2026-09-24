"""Your resume as the app keeps it: LaTeX source (or an uploaded PDF), compiled to PDF with pdflatex.

  kv "resume"      {filename, kind: "tex"|"pdf", tex, rev, updated_at}   ← synced (user.enc, part "resume")
  data/resume.pdf  the compiled/uploaded PDF, plus kv "resume_pdf_rev"   ← synced as resume_pdf.enc

`rev` is a short content hash, computed the same way in web/static.js, so every device and the GitHub
Action agree on which PDF belongs to which source.
"""
from __future__ import annotations

import glob
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple

# Packages the resume template needs; a fresh TinyTeX lacks some of them.
BASE_PACKAGES = ["titlesec", "enumitem", "fontawesome5", "cm-super", "xcolor", "hyperref", "tools", "graphics", "geometry",
                 "psnfss", "palatino", "times", "helvetic", "charter", "fpl", "mathpazo", "lm"]      # + the editor's fonts


class CompileError(RuntimeError):
    def __init__(self, message: str, log: str = ""):
        super().__init__(message)
        self.log = log


def rev_of(data: bytes | str) -> str:
    return hashlib.sha1(data.encode() if isinstance(data, str) else data).hexdigest()[:12]


def pdflatex() -> Optional[str]:
    """pdflatex from $PDFLATEX, PATH, or a user-level TinyTeX (~/Library/TinyTeX on macOS, ~/.TinyTeX on Linux)."""
    for cand in [os.environ.get("PDFLATEX"), shutil.which("pdflatex"),
                 *glob.glob(os.path.expanduser("~/Library/TinyTeX/bin/*/pdflatex")),
                 *glob.glob(os.path.expanduser("~/.TinyTeX/bin/*/pdflatex"))]:
        if cand and os.access(cand, os.X_OK):
            return cand
    return None


def _first_error(log: str) -> str:
    """The first '! ...' error from a TeX log, with the line number, e.g. 'Undefined control sequence (line 42)'."""
    m = re.search(r"^! (.+?)$(?:.*?^l\.(\d+))?", log, re.M | re.S)
    if not m:
        return "LaTeX failed (see the log)"
    return f"{m.group(1).strip()}" + (f" (line {m.group(2)})" if m.group(2) else "")


def compile_tex(tex: str, timeout: float = 120, install_missing: bool = True) -> bytes:
    """Compile LaTeX source to PDF bytes. Missing packages are installed once with TinyTeX's tlmgr."""
    exe = pdflatex()
    if not exe:
        raise CompileError("LaTeX isn't installed on this machine (install TinyTeX: https://yihui.org/tinytex/).")
    tried = set()
    with tempfile.TemporaryDirectory(prefix="resume-") as tmp:
        src = Path(tmp) / "resume.tex"
        src.write_text(tex)
        while True:
            try:
                r = subprocess.run([exe, "-interaction=nonstopmode", "-halt-on-error", "-no-shell-escape", src.name],
                                   cwd=tmp, capture_output=True, text=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                raise CompileError("LaTeX took too long (over 2 minutes)")
            log = (Path(tmp) / "resume.log").read_text(errors="ignore") if (Path(tmp) / "resume.log").exists() else r.stdout
            missing = re.search(r"File `([\w-]+)\.(?:sty|cls)' not found", log)
            if r.returncode and missing and install_missing and missing.group(1) not in tried:
                tried.add(missing.group(1))
                if _tlmgr_install(exe, missing.group(1)):
                    continue
            pdf = Path(tmp) / "resume.pdf"
            if r.returncode or not pdf.exists():
                raise CompileError(_first_error(log), log[-4000:])
            return pdf.read_bytes()


def _tlmgr_install(exe: str, name: str) -> bool:
    tlmgr = Path(exe).with_name("tlmgr")
    if not tlmgr.exists():
        return False
    pkg = name
    try:   # the .sty name usually is the package name; ask tlmgr when it isn't
        out = subprocess.run([str(tlmgr), "search", "--global", "--file", f"/{name}.sty"],
                             capture_output=True, text=True, timeout=120).stdout
        m = re.search(r"^([\w-]+):\s*$", out, re.M)
        pkg = m.group(1) if m else name
        return subprocess.run([str(tlmgr), "install", pkg], capture_output=True, timeout=600).returncode == 0
    except Exception:
        return False


def pdf_text(pdf: bytes) -> str:
    from io import BytesIO
    from pypdf import PdfReader
    return "\n".join((p.extract_text() or "") for p in PdfReader(BytesIO(pdf)).pages)


def source_file(res: dict, folder: Path, pdf: Optional[bytes] = None) -> Tuple[Path, str]:
    """Write the resume where build_profile() can read it; returns (path, rev)."""
    folder.mkdir(parents=True, exist_ok=True)
    if res.get("kind") == "tex":
        p = folder / "resume.tex"
        p.write_text(res["tex"])
    else:
        p = folder / "resume.pdf"
        if pdf is not None:
            p.write_bytes(pdf)
    return p, res.get("rev", "")


class ResumeStore:
    """The resume record in the database plus its PDF on disk (next to the database, in resume/)."""

    def __init__(self, db, folder: Optional[Path] = None):
        self.db = db
        self.folder = folder or Path(db.path).parent / "resume"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.pdf_path = self.folder / "resume.pdf"

    # -- reading ---------------------------------------------------------------------------------------
    def get(self) -> Optional[dict]:
        return self.db.get("resume")

    def pdf(self) -> Optional[bytes]:
        return self.pdf_path.read_bytes() if self.pdf_path.exists() else None

    @property
    def pdf_rev(self) -> Optional[str]:
        return self.db.get("resume_pdf_rev") if self.pdf_path.exists() else None

    def public(self) -> Optional[dict]:
        res = self.get()
        if not res:
            return None
        return {**{k: v for k, v in res.items() if k != "source_path"}, "pdf_rev": self.pdf_rev,
                "pdf_current": bool(res.get("rev")) and self.pdf_rev == res.get("rev"),
                "latex": pdflatex() is not None, "synced_to": res.get("source_path")}

    def text_source(self) -> Tuple[Path, str]:
        """A file build_profile() can read, for the current resume."""
        res = self.get()
        return source_file(res, self.folder / "build", self.pdf() if res.get("kind") == "pdf" else None)

    # -- writing -------------------------------------------------------------------------------------
    def set_pdf(self, pdf: bytes, rev: str) -> None:
        tmp = self.pdf_path.with_suffix(".tmp")
        tmp.write_bytes(pdf)
        tmp.replace(self.pdf_path)
        self.db.put("resume_pdf_rev", rev)

    def _record(self, **fields) -> dict:
        from .db import now
        res = {**(self.get() or {}), **fields, "updated_at": now()}
        self.db.put("resume", res)
        self.db.touch("resume")
        return res

    def _keep_version(self, name: str, data: bytes) -> None:
        from .db import now
        v = self.folder / "versions"
        v.mkdir(exist_ok=True)
        (v / f"{now().replace(':', '')}-{name}").write_bytes(data)
        for old in sorted(v.iterdir())[:-40]:          # the last 40 versions are plenty
            old.unlink()

    def save_tex(self, tex: str, filename: Optional[str] = None, model: Optional[dict] = None) -> dict:
        """Save new LaTeX source and compile it. The source is saved even if LaTeX fails (the error is
        recorded and the previous PDF stays), so edits are never lost."""
        prev = self.get() or {}
        if prev.get("kind") == "tex" and prev.get("tex"):
            self._keep_version(prev.get("filename") or "resume.tex", prev["tex"].encode())
        rev = rev_of(tex)
        res = self._record(kind="tex", tex=tex, rev=rev, filename=filename or prev.get("filename") or "resume.tex",
                           error=None, model=model, model_rev=rev if model else None)   # the visual editor's sections
        self.compile_current()
        self._write_back()
        return self.get() or res

    def save_pdf(self, pdf: bytes, filename: str) -> dict:
        rev = rev_of(pdf)
        self.set_pdf(pdf, rev)
        return self._record(kind="pdf", tex=None, rev=rev, filename=filename, error=None, source_path=None)

    def compile_current(self) -> bool:
        res = self.get()
        if not res or res.get("kind") != "tex":
            return False
        if self.pdf_rev == res["rev"]:
            return True
        try:
            self.set_pdf(compile_tex(res["tex"]), res["rev"])
            if res.get("error"):
                self.db.put("resume", {**res, "error": None})
            return True
        except CompileError as e:
            self.db.put("resume", {**res, "error": {"rev": res["rev"], "message": str(e)}})
            return False

    def _write_back(self) -> None:
        """Keep the file the resume was imported from (e.g. ~/Documents/…/Resume.tex and its .pdf) current."""
        res = self.get() or {}
        src = res.get("source_path")
        if not src or res.get("kind") != "tex":
            return
        src = Path(src)
        try:
            if src.exists() and src.read_text(errors="ignore") != res["tex"]:
                self._keep_version("original-" + src.name, src.read_bytes())
            src.write_text(res["tex"])
            if self.pdf_rev == res["rev"]:
                src.with_suffix(".pdf").write_bytes(self.pdf())
        except OSError:
            pass

    def import_file(self, path: Path) -> Optional[dict]:
        """First start: adopt an existing resume file (and its PDF, if it's up to date) as the app's copy."""
        if self.get() or not path.exists():
            return self.get()
        if path.suffix.lower() == ".pdf":
            return self.save_pdf(path.read_bytes(), path.name)
        tex = path.read_text(errors="ignore")
        res = self._record(kind="tex", tex=tex, rev=rev_of(tex), filename=path.name, error=None,
                           source_path=str(path))
        sibling = path.with_suffix(".pdf")
        if sibling.exists() and sibling.stat().st_mtime >= path.stat().st_mtime:
            self.set_pdf(sibling.read_bytes(), res["rev"])
        return res
