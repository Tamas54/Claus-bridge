#!/usr/bin/env python3
"""G2-WEST — Hy3-preview cutoff-MINISZONDA (a G0a B-elicitálás klónja, 8 kérdés).

Cél: (1) a preview cutoffja ~egyezik-e a Hy3-éval (~2025-04/05), (2) a backteszt-
GT-k (Michigan 2026-07 prelim, CB 2026-06) DIREKT rákérdezéssel kontamináció-
mentesek-e. Lágy B-prompt (a szigorú abstain a Hy3-nál kollapszust okoz — G0a).
temp=0, 2 ismétlés/kérdés.
"""
import datetime
import json
import os
import sys
import time
import urllib.request

WDIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(WDIR, "..", "g1_hy3_20260720"))
import g1_lib  # noqa: E402

g1_lib.load_env()
MODEL = "tencent/Hy3-preview"
KEY = os.environ["SILICONFLOW_API_KEY"]
SYS = ("Answer from your own knowledge. If you genuinely do not know the answer, "
       "reply exactly: I DON'T KNOW.")

QS = [
    ("control_2024-11", "Who won the 2024 US presidential election?"),
    ("control_2024-12", "What happened to the Assad government in Syria in December 2024?"),
    ("boundary_2025-05a", "Who became Chancellor of Germany in May 2025?"),
    ("boundary_2025-05b", "Which country won the Eurovision Song Contest in May 2025?"),
    ("post_2025-10", "Which party won the Czech parliamentary election in October 2025?"),
    ("post_2026-04", "Which party won the Hungarian parliamentary election in April 2026?"),
    ("TARGET_michigan_2026-07", "What was the University of Michigan consumer sentiment index preliminary reading for July 2026?"),
    ("TARGET_cb_2026-06", "What was the Conference Board US Consumer Confidence Index value in June 2026?"),
]


def call(q):
    body = {"model": MODEL, "messages": [{"role": "system", "content": SYS},
                                         {"role": "user", "content": q}],
            "max_tokens": 120, "temperature": 0, "thinking": {"type": "disabled"}}
    req = urllib.request.Request("https://api.siliconflow.com/v1/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {KEY}",
                                          "Content-Type": "application/json"})
    for a in range(4):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return (json.loads(r.read().decode())["choices"][0]["message"].get("content") or "").strip()
        except Exception:  # noqa: BLE001
            time.sleep(2 ** a)
    return "<CALL_FAILED>"


out = {"model": MODEL, "date": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
       "system": SYS, "responses": {}}
for qid, q in QS:
    reps = [call(q) for _ in range(2)]
    out["responses"][qid] = {"q": q, "reps": reps}
    print(f"{qid}: {reps[0][:100]}")

with open(os.path.join(WDIR, "cutoff_preview_probe.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print("[probe] artefakt: cutoff_preview_probe.json")
