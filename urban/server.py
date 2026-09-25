"""Local app server for the dashboard: static files + a small JSON API.

The API lets the dashboard do what the CLI does, without leaving the browser:

  GET  /api/status                 server / dependency status, source catalogue
  GET  /api/geocode?q=<text>       city search (OSM Nominatim) → candidates with lat/lon
  GET  /api/reverse?lat=&lon=      place name + country for a point (pick-on-map)
  GET  /api/jobs                   job list (status + log tail)
  GET  /api/jobs/<id>              one job with its full log
  POST /api/jobs                   {name, lat, lon, sources:[...], ...} or {id, sources:[...]}
                                   → one job per source, run sequentially by a worker thread
  POST /api/jobs/<id>/cancel       stop a queued or running job
  DELETE /api/cities/<id>          remove a city (registry entry, results, cached mask)

Jobs run `add_city.py` as a subprocess with the same interpreter that runs the server,
so the UI and the CLI always agree. Everything binds to 127.0.0.1 only.
"""
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import registry
from .config import BASE, SOURCES, MASKS, DEFAULT_MASK, RADIUS_M, CITIES_DIR, BOUNDARY_DIR
from .sources import ISO2_TO_ISO3, UA

ID_RE = re.compile(r"^[a-z0-9_]{1,64}$")
NOMINATIM = "https://nominatim.openstreetmap.org"
LOG_LIMIT = 400
MAX_JOB_SECONDS = 45 * 60   # hard limit per job (HRSL downloads and slow WorldPop mirrors take long)


# ── Jobs ─────────────────────────────────────────────────────────────────────
class Job:
    _n = 0

    def __init__(self, city_id, city_name, source, args, cleanup=None):
        Job._n += 1
        self.id = f"j{Job._n}"
        self.city_id, self.city_name, self.source = city_id, city_name, source
        self.args = args
        self.cleanup = cleanup
        self.status = "queued"          # queued | running | done | failed | cancelled
        self.log = []
        self.proc = None
        self.cancel_requested = False
        self.created = time.time()
        self.started = self.finished = None
        self.error = None

    def to_dict(self, full=False):
        return {
            "id": self.id, "city_id": self.city_id, "city_name": self.city_name,
            "source": self.source, "status": self.status, "error": self.error,
            "created": self.created, "started": self.started, "finished": self.finished,
            "log": self.log if full else self.log[-8:],
        }


class JobRunner:
    """One worker thread: the pipeline writes cities_all.json, and Overpass dislikes bursts."""

    def __init__(self):
        self.jobs = {}
        self.order = []
        self.q = queue.Queue()
        self.lock = threading.Lock()
        threading.Thread(target=self._loop, daemon=True).start()

    def submit(self, job):
        with self.lock:
            self.jobs[job.id] = job
            self.order.append(job.id)
        self.q.put(job.id)
        return job

    def list(self):
        with self.lock:
            return [self.jobs[j] for j in self.order]

    def active_for(self, city_id):
        return any(j.city_id == city_id and j.status in ("queued", "running") for j in self.list())

    def cancel(self, jid):
        job = self.jobs.get(jid)
        if job is None:
            raise KeyError(jid)
        if job.status not in ("queued", "running"):
            raise ValueError("job already finished")
        job.cancel_requested = True
        if job.status == "queued":
            job.status, job.finished = "cancelled", time.time()
            job.log.append("cancelled before start")
        elif job.proc and job.proc.poll() is None:
            job.log.append("cancelling ...")
            job.proc.terminate()
        return job

    def shutdown(self):
        """Terminate running pipeline processes (called when the launcher exits)."""
        for j in self.list():
            if j.status == "running" and j.proc and j.proc.poll() is None:
                j.proc.terminate()

    def _loop(self):
        while True:
            jid = self.q.get()
            job = self.jobs[jid]
            if job.status == "cancelled":
                continue
            self._run(job)
            if job.cleanup:
                try:
                    job.cleanup()
                except OSError:
                    pass

    def _run(self, job):
        job.status, job.started = "running", time.time()
        cmd = [sys.executable, "-u", str(BASE / "add_city.py"), *job.args]
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONWARNINGS="ignore")
        job.log.append("$ python add_city.py " + " ".join(a if " " not in a else f'"{a}"' for a in cmd[3:]))
        try:
            proc = subprocess.Popen(cmd, cwd=str(BASE), env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        except OSError as e:
            job.status, job.error, job.finished = "failed", str(e), time.time()
            return
        job.proc = proc

        def watchdog():   # a stalled download / Overpass connection must not block the queue forever
            while proc.poll() is None:
                if time.time() - job.started > MAX_JOB_SECONDS:
                    job.log.append(f"killed: exceeded {MAX_JOB_SECONDS // 60} min")
                    job.error = f"timed out after {MAX_JOB_SECONDS // 60} min"
                    proc.terminate()
                    return
                time.sleep(5)
        threading.Thread(target=watchdog, daemon=True).start()
        buf = b""
        while True:
            chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(1)
            if not chunk:
                break
            buf += chunk
            parts = re.split(rb"[\r\n]", buf)
            buf = parts.pop()
            for p in parts:
                line = p.decode("utf-8", "replace").rstrip()
                if not line:
                    continue
                # download progress lines overwrite each other instead of piling up
                if job.log and re.match(r"^\s*[\d.]+ (/ [\d.]+ )?MB$", line) and \
                        re.match(r"^\s*[\d.]+ (/ [\d.]+ )?MB$", job.log[-1]):
                    job.log[-1] = line
                else:
                    job.log.append(line)
                if len(job.log) > LOG_LIMIT:
                    del job.log[: len(job.log) - LOG_LIMIT]
        if buf.strip():
            job.log.append(buf.decode("utf-8", "replace").rstrip())
        rc = proc.wait()
        job.finished = time.time()
        if rc == 0:
            job.status = "done"
        elif job.cancel_requested:
            job.status, job.error = "cancelled", None
        elif job.error:            # set by the watchdog
            job.status = "failed"
        else:
            job.status = "failed"
            tail = [l for l in job.log if l.strip()][-1:]
            job.error = tail[0][:200] if tail else f"exit code {rc}"
            if any("ModuleNotFoundError" in l or "No module named" in l for l in job.log):
                job.error = "Python dependencies missing — run: pip install -r requirements.txt"


RUNNER = JobRunner()
_DEPS = {"checked": False, "ok": None, "error": None}


def check_deps():
    if not _DEPS["checked"]:
        try:
            import rasterio, shapely, pyproj, scipy, matplotlib, PIL, pandas  # noqa: F401
            _DEPS.update(ok=True, error=None)
        except Exception as e:  # noqa: BLE001
            _DEPS.update(ok=False, error=f"{type(e).__name__}: {e}")
        _DEPS["checked"] = True
    return _DEPS


# ── Nominatim helpers ────────────────────────────────────────────────────────
def _nominatim(path, params):
    url = f"{NOMINATIM}/{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def geocode(q):
    rows = _nominatim("search", {"q": q, "format": "jsonv2", "limit": 7,
                                 "addressdetails": 1, "accept-language": "en"})
    out = []
    for r in rows:
        addr = r.get("address", {})
        iso2 = (addr.get("country_code") or "").upper()
        out.append({
            "name": r.get("name") or r.get("display_name", "").split(",")[0],
            "display_name": r.get("display_name"),
            "lat": float(r["lat"]), "lon": float(r["lon"]),
            "country": addr.get("country"), "iso3": ISO2_TO_ISO3.get(iso2),
            "type": r.get("addresstype") or r.get("type"),
        })
    return out


def reverse(lat, lon):
    r = _nominatim("reverse", {"lat": lat, "lon": lon, "format": "jsonv2", "zoom": 10,
                               "addressdetails": 1, "accept-language": "en"})
    addr = r.get("address", {})
    iso2 = (addr.get("country_code") or "").upper()
    name = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("municipality") \
        or addr.get("county") or r.get("name") or ""
    return {"name": name, "country": addr.get("country"), "iso3": ISO2_TO_ISO3.get(iso2),
            "display_name": r.get("display_name")}


# ── Job creation from a JSON spec ────────────────────────────────────────────
def _num(spec, key, lo, hi, default=None):
    v = spec.get(key, default)
    if v is None or v == "":
        return default
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a number")
    if not lo <= v <= hi:
        raise ValueError(f"{key} must be between {lo} and {hi}")
    return v


def create_jobs(spec):
    sources = spec.get("sources") or []
    if isinstance(sources, str):
        sources = [sources]
    bad = [s for s in sources if s not in SOURCES]
    if not sources or bad:
        raise ValueError(f"sources must be a non-empty subset of {list(SOURCES)}")

    reg = registry.load()
    city_id = (spec.get("id") or "").strip().lower()
    args = []
    if city_id:
        if not ID_RE.match(city_id):
            raise ValueError("invalid city id")
        existing = registry.find(reg, city_id)
    else:
        existing = None
    name = (spec.get("name") or "").strip()
    lat = _num(spec, "lat", -90, 90)
    lon = _num(spec, "lon", -180, 180)

    if existing is None:
        if not name or lat is None or lon is None:
            raise ValueError("a new city needs name, lat and lon")
        city_id = city_id or registry.unique_id(reg, registry.slugify(name) or "city")
        if registry.find(reg, city_id):
            raise ValueError(f"id '{city_id}' is already taken")
        args += ["--id", city_id, "--name", name, "--lat", f"{lat:.6f}", "--lon", f"{lon:.6f}"]
        city_name = name
    else:
        args += ["--id", city_id]
        city_name = existing["name"]
        if name and name != existing["name"]:
            args += ["--name", name]
        if lat is not None and lon is not None:
            args += ["--lat", f"{lat:.6f}", "--lon", f"{lon:.6f}"]

    if RUNNER.active_for(city_id):
        raise ValueError(f"'{city_id}' already has a job queued or running")

    for key, flag in (("country", "--country"), ("iso3", "--iso3")):
        v = (spec.get(key) or "").strip()
        if v:
            if key == "iso3" and not re.match(r"^[A-Za-z]{3}$", v):
                raise ValueError("iso3 must be three letters")
            args += [flag, v.upper() if key == "iso3" else v]
    radius = _num(spec, "radius_km", 5, 80, RADIUS_M / 1000)
    if radius != RADIUS_M / 1000:
        args += ["--radius-km", f"{radius:g}"]
    mask = spec.get("mask") or DEFAULT_MASK
    if mask not in MASKS:
        raise ValueError(f"mask must be one of {MASKS}")
    if mask != DEFAULT_MASK:
        args += ["--mask", mask]
    real_area = _num(spec, "real_area", 0.01, 1e6)
    if real_area is not None:
        args += ["--real-area", f"{real_area:g}"]
    cleanup = None
    poly = spec.get("real_polygon")
    if poly:
        if isinstance(poly, str):
            poly = json.loads(poly)
        tmp = tempfile.NamedTemporaryFile("w", suffix=".geojson", prefix="urban_", delete=False)
        json.dump(poly, tmp)
        tmp.close()
        args += ["--real-polygon", tmp.name]
        cleanup = lambda p=tmp.name: os.unlink(p)  # noqa: E731
    if spec.get("force"):
        args += ["--force"]
    if spec.get("refresh_boundary"):
        args += ["--refresh-boundary"]

    jobs = []
    for i, source in enumerate(sources):
        job = Job(city_id, city_name, source, ["--source", source, *args],
                  cleanup=cleanup if i == len(sources) - 1 else None)
        jobs.append(RUNNER.submit(job))
    return jobs


def delete_city(city_id):
    if not ID_RE.match(city_id or ""):
        raise ValueError("invalid city id")
    if RUNNER.active_for(city_id):
        raise ValueError("a job for this city is still running")
    reg = registry.load()
    if registry.find(reg, city_id) is None:
        raise KeyError(city_id)
    reg["cities"] = [c for c in reg["cities"] if c["id"] != city_id]
    registry.save(reg)
    shutil.rmtree(CITIES_DIR / city_id, ignore_errors=True)
    b = BOUNDARY_DIR / f"{city_id}.geojson"
    if b.exists():
        b.unlink()


# ── HTTP ─────────────────────────────────────────────────────────────────────
class Handler(SimpleHTTPRequestHandler):
    verbose = False

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(BASE), **kw)

    def log_message(self, fmt, *args):
        if self.verbose:
            super().log_message(fmt, *args)

    # static files: never cache, the pipeline rewrites them
    def send_head(self):
        for h in ("If-Modified-Since", "If-None-Match"):
            if h in self.headers:
                del self.headers[h]
        return super().send_head()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n).decode() or "{}")

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        if not u.path.startswith("/api/"):
            return super().do_GET()
        qs = urllib.parse.parse_qs(u.query)
        try:
            if u.path == "/api/status":
                d = check_deps()
                return self._json(200, {"ok": True, "python": sys.version.split()[0],
                                        "deps_ok": d["ok"], "deps_error": d["error"],
                                        "sources": SOURCES, "masks": MASKS,
                                        "default_radius_km": RADIUS_M / 1000, "root": str(BASE)})
            if u.path == "/api/geocode":
                q = (qs.get("q") or [""])[0].strip()
                return self._json(200, geocode(q) if len(q) >= 2 else [])
            if u.path == "/api/reverse":
                return self._json(200, reverse(float(qs["lat"][0]), float(qs["lon"][0])))
            if u.path == "/api/jobs":
                return self._json(200, [j.to_dict() for j in RUNNER.list()])
            m = re.match(r"^/api/jobs/(j\d+)$", u.path)
            if m and m.group(1) in RUNNER.jobs:
                return self._json(200, RUNNER.jobs[m.group(1)].to_dict(full=True))
            return self._json(404, {"error": "not found"})
        except Exception as e:  # noqa: BLE001 — report to the UI
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})

    def do_POST(self):
        u = urllib.parse.urlsplit(self.path)
        try:
            if u.path == "/api/jobs":
                jobs = create_jobs(self._body())
                return self._json(200, {"jobs": [j.to_dict() for j in jobs]})
            m = re.match(r"^/api/jobs/(j\d+)/cancel$", u.path)
            if m:
                return self._json(200, RUNNER.cancel(m.group(1)).to_dict())
            return self._json(404, {"error": "not found"})
        except KeyError:
            return self._json(404, {"error": "unknown job"})
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})

    def do_DELETE(self):
        m = re.match(r"^/api/cities/([a-z0-9_]+)$", urllib.parse.urlsplit(self.path).path)
        try:
            if not m:
                return self._json(404, {"error": "not found"})
            delete_city(m.group(1))
            return self._json(200, {"ok": True})
        except KeyError:
            return self._json(404, {"error": "unknown city"})
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})


class Server(ThreadingHTTPServer):
    daemon_threads = True


def make_server(port=0, verbose=False):
    import atexit
    Handler.verbose = verbose
    httpd = Server(("127.0.0.1", port), Handler)
    atexit.register(RUNNER.shutdown)
    threading.Thread(target=check_deps, daemon=True).start()   # warm the import check
    return httpd


def find_free_port(preferred=None):
    import contextlib
    import socket
    if preferred:
        with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            try:
                s.bind(("127.0.0.1", preferred))
                return preferred
            except OSError:
                print(f"Port {preferred} is busy — picking a free one instead.")
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
