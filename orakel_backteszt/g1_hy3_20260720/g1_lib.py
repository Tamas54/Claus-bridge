"""OPERATION PYTHIA — G1 replikációs segédréteg.

NEM módosít harnesst: a wrapperek importálják a meglévő futtatókat, és ez a réteg
csak (a) a G0a/B rate-limit szabály szerinti token-bucket pacinget, (b) a hívás-
számláló/nyers-válasz naplót, (c) a corpus-hash + artefakt-író segédeket adja.

Rate-szabály (orakel_backteszt/cutoff_szonda_20260720/rate_limit_meres.md):
  konkurrencia 24, max hívás/perc = min(100, 300000 / token_per_hívás),
  backoff a harness-call-okban már él (2^n + jitter).
"""
import hashlib
import json
import os
import threading
import time

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
G1DIR = os.path.dirname(os.path.abspath(__file__))
MODEL = "tencent/Hy3"
CONCURRENCY = 24
CHARS_PER_TOKEN = 3.2   # HU/CZ/PT/PL vegyes szövegre konzervatív becslés


def load_env():
    """SF-kulcs a ~/videoforge/.env-ből (kanonikus a G1-hez), OPENAI a Bridge .env-ből.
    setdefault → a harness-beli dotenv (override nélkül) már nem írja felül."""
    for path in (os.path.expanduser("~/videoforge/.env"), os.path.join(REPO, ".env")):
        try:
            for line in open(path, encoding="utf-8"):
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:]
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        except OSError:
            pass


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def pace_for(est_tokens_per_call, tpm_budget=300_000, cap=100.0):
    """Hívás/perc plafon a G0a/B méretezési szabály szerint."""
    return min(cap, max(1.0, tpm_budget / max(1.0, float(est_tokens_per_call))))


def est_tokens(system, user, max_tokens=160):
    return int((len(system) + len(user)) / CHARS_PER_TOKEN) + max_tokens


class Pacer:
    """Token-bucket pacing + hívás-számláló + nyersválasz-folyam (szál-biztos)."""

    def __init__(self, calls_per_min, capture_raw=True):
        self.calls_per_min = calls_per_min
        self.interval = 60.0 / max(1e-9, calls_per_min)
        self._lock = threading.Lock()
        self._next = 0.0
        self.n_calls = 0
        self.prompt_chars = 0
        self.completion_chars = 0
        self.capture_raw = capture_raw
        self.raw_stream = []
        self.t0 = time.time()

    def _acquire(self):
        with self._lock:
            t = max(time.time(), self._next)
            self._next = t + self.interval
        w = t - time.time()
        if w > 0:
            time.sleep(w)

    def wrap(self, fn):
        """A harness hívó-függvényét (system, user az első két pozicionális arg)
        pacingelt + naplózott változatra cseréli. A fn maga változatlan marad."""
        def paced(*args, **kwargs):
            self._acquire()
            txt = fn(*args, **kwargs)
            s = args[0] if len(args) > 0 and isinstance(args[0], str) else ""
            u = args[1] if len(args) > 1 and isinstance(args[1], str) else ""
            with self._lock:
                self.n_calls += 1
                self.prompt_chars += len(s) + len(u)
                self.completion_chars += len(txt or "")
                if self.capture_raw:
                    self.raw_stream.append({"i": self.n_calls, "response": txt})
            return txt
        return paced

    def stats(self):
        return {
            "n_calls": self.n_calls,
            "calls_per_min_limit": round(self.calls_per_min, 1),
            "elapsed_s": round(time.time() - self.t0, 1),
            "prompt_chars": self.prompt_chars,
            "completion_chars": self.completion_chars,
            "est_prompt_tokens": int(self.prompt_chars / CHARS_PER_TOKEN),
            "est_completion_tokens": int(self.completion_chars / CHARS_PER_TOKEN),
        }


def write_artifact(relpath, payload):
    out = os.path.join(G1DIR, relpath)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[g1] artefakt: {out}")
    return out
