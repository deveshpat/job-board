"""A local stand-in for the parts of the GitHub REST API the app uses (tests and manual checks only).

  contents   GET/PUT /repos/{o}/{r}/contents/{path}?ref=data   (sha-checked writes → 409 on conflict)
  secrets    GET public-key, PUT/DELETE secrets/{name}          (sealed boxes really decrypted here)
  actions    POST workflows/daily.yml/dispatches, GET runs
  git data   POST git/blobs, git/trees, git/commits, git/refs, PUT pages (used by "Publish to GitHub")
  GET /_state → everything, for assertions.
"""
import base64
import hashlib
import itertools

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, Response
from nacl.public import PrivateKey, SealedBox

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"], expose_headers=["x-oauth-scopes"])

BRANCHES = {"data": {}, "main": {}}     # branch -> {path: (sha, bytes)}
FILES = BRANCHES["data"]
SECRETS = {}
RUNS = []
REFS = {}
KEY = PrivateKey.generate()
IDS = itertools.count(1)


def sha_of(b: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(b) + b).hexdigest()


def seed(files: dict) -> None:
    FILES.clear()
    REFS["refs/heads/data"] = "seeded"
    for path, data in files.items():
        data = data if isinstance(data, bytes) else data.encode()
        FILES[path] = (sha_of(data), data)


@app.get("/_state")
def state():
    return {"files": {p: s for p, (s, _) in FILES.items()}, "main": sorted(BRANCHES.get("main", {})),
            "secrets": SECRETS, "runs": RUNS, "refs": list(REFS)}


@app.get("/repos/{o}/{r}/contents/{path:path}")
def get_file(o: str, r: str, path: str, request: Request, ref: str = "main"):
    files = BRANCHES.get(ref, {})
    if path not in files:
        raise HTTPException(404, "Not Found")
    sha, data = files[path]
    if "raw" in request.headers.get("accept", ""):
        return PlainTextResponse(data.decode())
    big = len(data) > 1_000_000                       # like GitHub: no inline content above 1 MB
    return {"sha": sha, "size": len(data), "encoding": "none" if big else "base64",
            "content": "" if big else base64.b64encode(data).decode()}


@app.put("/repos/{o}/{r}/contents/{path:path}")
async def put_file(o: str, r: str, path: str, request: Request):
    body = await request.json()
    files = BRANCHES.setdefault(body.get("branch", "main"), {})
    if path in files and body.get("sha") != files[path][0]:
        raise HTTPException(409, "sha mismatch")
    data = base64.b64decode(body["content"])
    files[path] = (sha_of(data), data)
    ref = f"refs/heads/{body.get('branch', 'main')}"
    if ref not in REFS:                       # the contents API can create a repo's first commit/branch
        commit_sha = hashlib.sha1(("first:" + ref).encode()).hexdigest()
        COMMITS[commit_sha] = {"tree": _tree_of(files)}
        REFS[ref] = commit_sha
    return {"content": {"path": path, "sha": files[path][0]}}


def _tree_of(files):
    sha = hashlib.sha1(repr(sorted(files)).encode()).hexdigest()
    for p, (s, d) in files.items():
        BLOBS[s] = d
    TREES[sha] = {"base": None, "entries": [{"path": p, "sha": s} for p, (s, _) in files.items()]}
    return sha


@app.get("/repos/{o}/{r}/actions/secrets/public-key")
def public_key(o: str, r: str):
    return {"key_id": "k1", "key": base64.b64encode(bytes(KEY.public_key)).decode()}


@app.put("/repos/{o}/{r}/actions/secrets/{name}")
async def put_secret(o: str, r: str, name: str, request: Request):
    body = await request.json()
    SECRETS[name] = SealedBox(KEY).decrypt(base64.b64decode(body["encrypted_value"])).decode()
    return Response(status_code=201)


@app.delete("/repos/{o}/{r}/actions/secrets/{name}")
def del_secret(o: str, r: str, name: str):
    SECRETS.pop(name, None)
    return Response(status_code=204)


@app.post("/repos/{o}/{r}/actions/workflows/{wf}/dispatches")
async def dispatch(o: str, r: str, wf: str, request: Request):
    done = wf == "pages.yml"                         # a Pages deploy "finishes" at once here
    RUNS.insert(0, {"id": next(IDS), "workflow": wf, "status": "completed" if done else "queued",
                    "conclusion": "success" if done else None, "inputs": (await request.json()).get("inputs")})
    return Response(status_code=204)


@app.get("/repos/{o}/{r}/actions/workflows/{wf}/runs")
def runs(o: str, r: str, wf: str):
    return {"workflow_runs": RUNS}


@app.post("/_finish_runs")
def finish_runs():
    for run in RUNS:
        run.update(status="completed", conclusion="success")
    return {"ok": True}


# -- git data API (orphan `data` branch creation) --------------------------------------------------
BLOBS, TREES, COMMITS = {}, {}, {}


def _require_not_empty():
    """Real GitHub: the git data API refuses to work until the repository has a first commit."""
    if not any(BRANCHES.values()):
        raise HTTPException(409, "Git Repository is empty.")


@app.post("/repos/{o}/{r}/git/blobs")
async def blob(o: str, r: str, request: Request):
    _require_not_empty()
    b = await request.json()
    data = base64.b64decode(b["content"]) if b.get("encoding") == "base64" else b["content"].encode()
    BLOBS[sha_of(data)] = data
    return {"sha": sha_of(data)}


@app.post("/repos/{o}/{r}/git/trees")
async def tree(o: str, r: str, request: Request):
    b = await request.json()
    sha = hashlib.sha1(repr(b).encode()).hexdigest()
    TREES[sha] = {"base": b.get("base_tree"), "entries": b["tree"]}
    return {"sha": sha}


def _files_of(tree_sha):
    t = TREES[tree_sha]
    out = dict(_files_of(t["base"])) if t["base"] else {}
    for e in t["entries"]:
        out[e["path"]] = (e["sha"], BLOBS[e["sha"]])
    return out


@app.get("/repos/{o}/{r}/git/commits/{sha}")
def get_commit(o: str, r: str, sha: str):
    return {"sha": sha, "tree": {"sha": COMMITS[sha]["tree"]}}


@app.post("/repos/{o}/{r}/git/commits")
async def commit(o: str, r: str, request: Request):
    b = await request.json()
    sha = hashlib.sha1(repr(b).encode()).hexdigest()
    COMMITS[sha] = b
    return {"sha": sha}


@app.post("/repos/{o}/{r}/git/refs")
async def ref(o: str, r: str, request: Request):
    b = await request.json()
    if b["ref"] in REFS:
        raise HTTPException(422, "Reference already exists")
    REFS[b["ref"]] = b["sha"]
    branch = b["ref"].split("/")[-1]
    BRANCHES.setdefault(branch, {}).clear()
    BRANCHES[branch].update(_files_of(COMMITS[b["sha"]]["tree"]))   # materialise for the contents API
    return {"ref": b["ref"]}


@app.patch("/repos/{o}/{r}/git/refs/heads/{branch}")
async def move_ref(o: str, r: str, branch: str, request: Request):
    b = await request.json()
    REFS[f"refs/heads/{branch}"] = b["sha"]
    BRANCHES.setdefault(branch, {}).update(_files_of(COMMITS[b["sha"]]["tree"]))
    return {"ref": f"refs/heads/{branch}"}


@app.get("/repos/{o}/{r}/git/ref/{ref:path}")
def get_ref(o: str, r: str, ref: str):
    _require_not_empty()
    if f"refs/{ref}" not in REFS:
        raise HTTPException(404, "Not Found")
    return {"ref": f"refs/{ref}", "object": {"sha": REFS[f"refs/{ref}"]}}


PAGES_ALLOWED = {"ok": True}
GENERATED = {}                                     # repos made from the template (setup page)


@app.get("/user")
def user():
    return Response('{"login": "newuser"}', media_type="application/json", headers={"x-oauth-scopes": "repo, workflow"})


@app.post("/repos/{o}/{r}/generate")
async def generate(o: str, r: str, request: Request):
    b = await request.json()
    GENERATED[f"{b['owner']}/{b['name']}"] = f"{o}/{r}"
    BRANCHES.setdefault("main", {})
    if not BRANCHES["main"]:
        data = b"# Job Board\n"
        BRANCHES["main"]["README.md"] = (sha_of(data), data)
        REFS["refs/heads/main"] = "generated"
    return Response(status_code=201)


@app.get("/repos/{o}/{r}")
def repo(o: str, r: str):
    tpl = GENERATED.get(f"{o}/{r}")
    if not tpl:
        raise HTTPException(404, "Not Found")
    return {"full_name": f"{o}/{r}", "template_repository": {"full_name": tpl}}


@app.get("/repos/{o}/{r}/branches/{b}")
def branch(o: str, r: str, b: str):
    if not BRANCHES.get(b):
        raise HTTPException(404, "Branch not found")
    return {"name": b}


@app.post("/repos/{o}/{r}/pages")
def pages(o: str, r: str):
    if not PAGES_ALLOWED["ok"]:              # a token without the Pages permission
        raise HTTPException(403, "Resource not accessible by personal access token")
    return Response(status_code=201)


def reset() -> None:
    for d in (BRANCHES["data"], BRANCHES["main"], SECRETS, REFS, BLOBS, TREES, COMMITS):
        d.clear()
    RUNS.clear()
    PAGES_ALLOWED["ok"] = True
