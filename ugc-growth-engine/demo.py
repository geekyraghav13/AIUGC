"""The judge demo: brief → understand → Gen 1 → render → simulate → insights → Gen 2 → render → simulate → improvement.

Run: python demo.py [path/to/brief.json]   (or the "Run Full Loop (Demo)" button on the dashboard Home page)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

from engine import cost, loop, variations
from engine import progress as prg
from engine.locks import app_lock
from engine.brief import load_brief, register_app
from engine.settings import env_int

DEMO_SCRIPTED = 24   # scripts written in Gen 1 for the demo (matrix still shows all ~300 combinations)


def run_full_demo(brief_path: str | Path, render_count: int = 6, generations: int = 2,
                  progress: Optional[Callable[[str], None]] = None,
                  render_progress: Optional[Callable] = None) -> dict[str, Any]:
    t0 = time.time()
    say = progress or (lambda m: print(m, flush=True))
    say("Registering app brief…")
    app = register_app(load_brief(brief_path))
    with app_lock(app.id, "full loop demo"), \
            prg.tracked(app.id, f"Full loop · {app.name}", prg.full_loop_steps(generations)):
        loop.ensure_generation_one(app.id, render_count=render_count, progress=say,
                                   scripted=env_int("DEMO_SCRIPTED", DEMO_SCRIPTED))
        res = loop.run_loop(app.id, generations=generations, simulated=True, render_count=render_count,
                            render=True, next_size=render_count, progress=say, render_progress=render_progress)
    res["matrix"] = variations.matrix_counts(app.id, 1)
    res["economics"] = cost.summary(app.id)
    res["wall_seconds"] = round(time.time() - t0, 1)
    say(f"Done in {res['wall_seconds']:.0f}s")
    return res


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "data/sample_briefs/fintech.json"
    out = run_full_demo(path)
    for g in out["generations"]:
        print(f"Gen {g['generation']}: {g['creatives']} creatives · {g['primary']:.2f} {g['metric']} · "
              f"CTR {g['ctr'] * 100:.2f}% (SIMULATED)")
