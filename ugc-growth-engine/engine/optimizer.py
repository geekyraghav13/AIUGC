"""Growth-loop optimizer: Thompson sampling per tag dimension, 70% exploit / 30% explore allocation."""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from typing import Any, Optional

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field
from sqlmodel import select

from engine.locks import locked
from engine import compliance, concepts as concepts_mod, llm, mock_content, scripts, tracking
from engine.brief import brief_of
from engine.db import (Concept, Creative, Insight, current_generation, get_app, latest_understanding, save,
                       session)
from engine.insights_perf import latest as latest_insight, successes_trials
from engine.localize import check_language, language_block
from engine.metrics import metrics_frame, primary_metric_name
from engine.settings import OUTPUTS_DIR, creator_styles, env_float, env_int, formats, languages, log, taxonomy
from engine.modes import MODES, compatible, mode_cost
from engine.variations import default_styles, dedupe

OPT_DIMS = ["language", "hook_type", "format", "creator_style", "message", "visual_mode"]
PRIOR_STRENGTH = 3000.0  # prior pseudo-trials (≈ one creative's views) centred on the overall rate


def _int(v: Any) -> Optional[int]:
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else int(v)


# ---------------------------------------------------------------- Thompson sampling

def arms(app_id: str, df: pd.DataFrame) -> dict[str, list[str]]:
    """Candidate values per dimension: everything allowed for this app plus anything already tested."""
    app = get_app(app_id)
    b = brief_of(app)
    u = latest_understanding(app_id)
    tested = {d: sorted(set(df[d].dropna().astype(str))) if not df.empty else [] for d in OPT_DIMS}
    base_styles = sorted(set(default_styles(b.brand_tone)) | set(tested["creator_style"]))
    out = {
        "language": [l for l in b.languages if l in languages()],
        "hook_type": list(taxonomy()["hook_types"]),
        "format": list(formats()),
        "creator_style": [s for s in creator_styles() if s in base_styles or env_int("EXPLORE_ALL_STYLES", 1)],
        "message": [m["id"] for m in (u.key_messages if u else [])],
        "visual_mode": list(MODES),
    }
    for d in OPT_DIMS:
        out[d] = sorted(set(out[d]) | set(tested[d]))
    return out


def posteriors(df: pd.DataFrame, metric: str, dim: str, values: list[str],
               market: Any = None) -> dict[str, tuple[float, float]]:
    """Beta(α, β) per arm with a prior centred on the overall rate; proven market patterns (D8) tilt the prior
    by their lift with at most `market_prior_strength` pseudo-creatives of weight."""
    from engine.research import cfg as research_cfg

    if df.empty:
        s_all, n_all = 0.0, 0.0
    else:
        s_all, n_all, _ = successes_trials(df, metric)
    p0 = (s_all + 1) / (n_all + 2) if n_all else 0.01
    views_per_creative = n_all / len(df) if not df.empty else PRIOR_STRENGTH
    out = {}
    for v in values:
        edge = market.edge(dim, v) if market is not None and market.available else None
        if edge is None:
            a0, b0 = p0 * PRIOR_STRENGTH, (1 - p0) * PRIOR_STRENGTH
        else:
            n0 = float(research_cfg()["market_prior_strength"]) * views_per_creative
            p = min(0.99, p0 * math.exp(max(-0.5, min(0.5, 0.5 * edge))))
            a0, b0 = p * n0, (1 - p) * n0
        if df.empty:
            out[v] = (a0, b0)
            continue
        g = df[df[dim].astype(str) == v]
        s, n, _ = successes_trials(g, metric) if not g.empty else (0.0, 0.0, 1.0)
        out[v] = (a0 + s, b0 + max(0.0, n - s))
    return out


def thompson(post: dict[str, tuple[float, float]], rng: np.random.Generator) -> str:
    keys = list(post)
    draws = [rng.beta(a, b) for a, b in post.values()]
    return keys[int(np.argmax(draws))]


class EffectModel:
    """Joint ridge-penalised Poisson model: successes_i ~ Poisson(trials_i · exp(b0 + Σ β_dim=value)).

    Estimating all tag effects jointly removes the confounding of per-dimension averages (e.g. a format that
    only looks good because it was paired with the winning hook type). A Laplace approximation gives a Gaussian
    posterior over β that Thompson sampling draws from; untested values keep the N(0, 1/λ) prior → exploration.

    With a MarketContext (D8), proven market patterns shift the prior mean of their tag values, with precision
    capped at `market_prior_strength` pseudo-creatives — so before any metrics exist the optimizer favours what
    works in the market, and our own data overrides it within a few generations.
    """

    def __init__(self, df: pd.DataFrame, metric: str, candidates: dict[str, list[str]], ridge: float = 2.0,
                 market: Any = None):
        self.cols = [(d, v) for d in OPT_DIMS for v in candidates.get(d, [])]
        self.index = {c: i for i, c in enumerate(self.cols)}
        k = len(self.cols)
        mu0, prec = np.zeros(k), np.full(k, ridge)
        s_bar = 20.0
        if not df.empty:
            s_all, _, _ = successes_trials(df, metric)
            s_bar = max(1.0, s_all / len(df))
        self.market_priors = market.optimizer_priors(s_bar) if market is not None and market.available else {}
        for key, (m, p) in self.market_priors.items():
            j = self.index.get(key)
            if j is not None:
                mu0[j], prec[j] = m, p
        self.prior_mean = mu0
        self.mean = mu0.copy()
        self.cov = np.diag(1.0 / prec)
        self.fitted = False
        if df.empty:
            return
        X = np.zeros((len(df), k))
        for r, row in enumerate(df.itertuples()):
            for d in OPT_DIMS:
                j = self.index.get((d, str(getattr(row, d, ""))))
                if j is not None:
                    X[r, j] = 1.0
        st = [successes_trials(df.iloc[[i]], metric)[:2] for i in range(len(df))]
        y = np.array([s for s, _ in st])
        n = np.maximum(np.array([t for _, t in st]), 1.0)
        b0 = np.log((y.sum() + 0.5) / n.sum())
        off = np.log(n) + b0

        def f(beta: np.ndarray) -> tuple[float, np.ndarray]:
            eta = off + X @ beta
            mu = np.exp(np.clip(eta, -30, 30))
            dev = beta - mu0
            loss = float(np.sum(mu - y * eta) + 0.5 * np.sum(prec * dev * dev))
            grad = X.T @ (mu - y) + prec * dev
            return loss, grad

        from scipy.optimize import minimize

        res = minimize(f, mu0.copy(), jac=True, method="L-BFGS-B")
        beta = res.x
        mu = np.exp(np.clip(off + X @ beta, -30, 30))
        H = X.T @ (X * mu[:, None]) + np.diag(prec)
        self.mean = beta
        self.cov = np.linalg.inv(H)
        self.fitted = True

    def effects(self, dim: str) -> dict[str, float]:
        """Posterior mean multiplier (exp β) per value of a dimension."""
        return {v: float(np.exp(self.mean[j])) for (d, v), j in self.index.items() if d == dim}

    def favourite(self, dim: str, among: Optional[list[str]] = None) -> str:
        eff = self.effects(dim)
        if among:
            eff = {v: e for v, e in eff.items() if v in among} or eff
        return max(eff, key=eff.get)

    def sample(self, rng: np.random.Generator) -> dict[str, str]:
        """One Thompson draw: best value per dimension under a posterior sample of β."""
        draw = rng.multivariate_normal(self.mean, self.cov, method="cholesky")
        best: dict[str, tuple[float, str]] = {}
        for (d, v), j in self.index.items():
            if d not in best or draw[j] > best[d][0]:
                best[d] = (draw[j], v)
        return {d: v for d, (_, v) in best.items()}


def least_tested(df: pd.DataFrame, dim: str, values: list[str], rng: np.random.Generator) -> str:
    counts = df[dim].astype(str).value_counts().to_dict() if not df.empty else {}
    lo = min(counts.get(v, 0) for v in values)
    cands = [v for v in values if counts.get(v, 0) == lo]
    return cands[int(rng.integers(len(cands)))]


# ---------------------------------------------------------------- guidance for the LLM

def guidance_text(ins: Optional[Insight]) -> str:
    if ins is None or not ins.findings:
        return "(no data yet)"
    f = ins.findings
    lines = []
    for d, rows in (f.get("dimensions") or {}).items():
        win = [r for r in rows if r.get("status") == "winner"]
        bad = [r for r in rows if r.get("status") == "underperformer"]
        if win:
            lines.append(f"WINNING {d}: {win[0]['value']} ({(win[0]['lift'] or 0) * 100:+.0f}% vs average)")
        if bad:
            lines.append(f"AVOID {d}: {', '.join(r['value'] for r in bad)}")
    if f.get("top"):
        lines.append("Best hooks so far: " + " | ".join(f"“{t['hook']}” ({t['language']}, {t['hook_type']})" for t in f["top"]))
    if f.get("bottom"):
        lines.append("Weakest hooks (don't copy): " + " | ".join(f"“{t['hook']}”" for t in f["bottom"]))
    for r in f.get("recommendations", []):
        lines.append(f"Recommendation: {r}")
    for s in f.get("stop_doing", []):
        lines.append(f"Stop: {s}")
    return "\n".join(lines) or "(no clear signal yet)"


# ---------------------------------------------------------------- slots

class SlotHook(BaseModel):
    slot_id: int
    hook: str
    hook_type: str = ""


class SlotHooks(BaseModel):
    hooks: list[SlotHook] = Field(default_factory=list)


def plan_slots(app_id: str, size: int, explore_ratio: float, seed: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Decide the tags of every creative in the next batch (no LLM yet)."""
    df = metrics_frame(app_id)
    metric = primary_metric_name(df)
    rng = np.random.default_rng(seed)
    candidates = arms(app_id, df)
    from engine.research.market import context

    market = context(app_id)
    model = EffectModel(df, metric, candidates, market=market)
    ts = lambda: model.sample(rng)  # noqa: E731 — one Thompson draw over all dimensions
    n_exploit = int(round(size * (1 - explore_ratio)))
    n_explore = size - n_exploit
    slots: list[dict[str, Any]] = []

    # --- exploit: children of the top 20% creatives + new concepts on the winning message
    ranked = df.sort_values(metric, ascending=False) if not df.empty else df
    top = ranked.head(max(1, int(np.ceil(len(ranked) * 0.2)))) if not ranked.empty else ranked
    n_new_concept = min(2, n_exploit // 4) if not top.empty else 0
    n_children = n_exploit - n_new_concept
    parents = top.to_dict("records")
    for i in range(n_children):
        if not parents:
            break
        p = parents[i % len(parents)]
        draw = ts()
        # children keep the parent's winning angle; the hook type (and sometimes format/style) follow the model
        slots.append({"origin": "exploit", "parent_creative_id": int(p["creative_id"]), "concept_id": _int(p["concept_id"]),
                      "parent_hook": p["hook"], "language": p["language"], "message": p["message"],
                      "persona": p["persona"],
                      "hook_type": draw["hook_type"] if i % 2 else p["hook_type"],
                      "format": draw["format"] if i % 3 == 2 else p["format"],
                      "creator_style": draw["creator_style"] if i % 4 == 3 else p["creator_style"],
                      "visual_mode": draw["visual_mode"] if i % 2 else (p.get("visual_mode") or draw["visual_mode"])})
    for _ in range(n_new_concept):
        slots.append({"origin": "exploit", "parent_creative_id": None, "concept_id": None, "new_concept": True,
                      **ts()})
    # exploit slots that had no parent (no data yet) become Thompson draws
    while len([s for s in slots if s["origin"] == "exploit"]) < n_exploit:
        slots.append({"origin": "exploit", "parent_creative_id": None, "concept_id": None, **ts()})

    # --- explore: under-tested values, one or two dimensions at a time
    for i in range(n_explore):
        s = {"origin": "explore", "parent_creative_id": None, "concept_id": None, **ts()}
        for d in rng.choice(OPT_DIMS, size=2, replace=False):
            s[d] = least_tested(df, d, candidates[d], rng)
        if i % 3 == 0:
            s["new_concept"] = True
        slots.append(s)

    # every slot must be shootable in its mode (creator style/format) and the batch affordable in Magnific credits
    from engine.production.assets import budget_left

    left = budget_left()
    for s in sorted(slots, key=lambda s: -mode_cost(s.get("visual_mode", "faceless"))):
        ok = compatible(s.get("format", ""), s.get("creator_style", ""))
        if s.get("visual_mode") not in ok:
            eff = model.effects("visual_mode")
            s["visual_mode"] = max(ok, key=lambda m: eff.get(m, 1.0))
        while mode_cost(s["visual_mode"]) > left:
            cheaper = [m for m in ok if mode_cost(m) < mode_cost(s["visual_mode"])]
            if not cheaper:
                break
            s["visual_mode"] = cheaper[0]
        left -= mode_cost(s["visual_mode"])
    allocation = {
        "size": size, "explore_ratio": explore_ratio, "exploit": n_exploit, "explore": n_explore, "metric": metric,
        "share": {d: dict(Counter(s[d] for s in slots)) for d in OPT_DIMS},
        "effects": {d: model.effects(d) for d in OPT_DIMS},
        "parents": [int(p["creative_id"]) for p in parents],
    }
    return slots, allocation


def _assign_concepts(app_id: str, slots: list[dict[str, Any]], generation: int, guidance: str) -> None:
    """Attach a concept to every slot: parent's concept, a new concept, or an existing one with that message."""
    with session() as s:
        existing = list(s.exec(select(Concept).where(Concept.app_id == app_id)).all())
    need_new = [sl for sl in slots if sl.get("new_concept")]
    fresh: list[Concept] = []
    if need_new:
        try:
            fresh = concepts_mod.run(app_id, n=len(need_new), generation=generation, guidance=guidance)
        except llm.LLMError as e:
            log.warning(f"new concepts failed: {e}")
    for sl, c in zip(need_new, fresh):
        sl["concept_id"] = c.id
        sl["message"], sl["persona"] = c.message, c.persona
        sl.setdefault("format", c.format)
    by_msg: dict[str, list[Concept]] = defaultdict(list)
    for c in existing + fresh:
        by_msg[c.message].append(c)
    for i, sl in enumerate(slots):
        if sl.get("concept_id"):
            continue
        pool = by_msg.get(sl["message"]) or existing or fresh
        c = pool[i % len(pool)]
        sl["concept_id"], sl["persona"], sl["message"] = c.id, c.persona, c.message


def _write_hooks(app_id: str, slots: list[dict[str, Any]], generation: int, guidance: str) -> None:
    app = get_app(app_id)
    b = brief_of(app)
    with session() as s:
        concept_map = {c.id: c for c in s.exec(select(Concept).where(Concept.app_id == app_id)).all()}
    by_lang: dict[str, list[int]] = defaultdict(list)
    for i, sl in enumerate(slots):
        by_lang[sl["language"]].append(i)
    for lang, idxs in by_lang.items():
        payload = []
        for i in idxs:
            sl, c = slots[i], concept_map.get(slots[i]["concept_id"])
            payload.append({"slot_id": i, "mode": sl["origin"], "hook_type": sl["hook_type"],
                            "concept": {"title": c.title, "pain_point": c.pain_point, "india_scenario": c.india_scenario}
                            if c else {}, "parent_hook": sl.get("parent_hook", "")})
        prompt = llm.render_prompt("hooks_targeted", app_name=b.app_name, one_liner=b.one_liner,
                                   generation=str(generation), language_block=language_block(lang),
                                   hook_types=taxonomy()["hook_types"], guidance=guidance, slots=payload,
                                   claims_to_avoid=", ".join(b.claims_to_avoid) or "(none)")

        def mock() -> dict[str, Any]:
            return {"hooks": [{"slot_id": i, "hook": mock_content.hook(lang, slots[i]["hook_type"], b.app_name,
                                                                        generation * 7 + i),
                               "hook_type": slots[i]["hook_type"]} for i in idxs]}

        try:
            res: SlotHooks = llm.complete_json(f"hooks_g{generation}_{lang}", prompt, SlotHooks, temperature=1.0,
                                               app_id=app_id, mock_payload=mock)
        except llm.LLMError as e:
            log.error(f"targeted hooks failed for {lang}: {e}")
            continue
        for h in res.hooks:
            if (h.slot_id in idxs and h.hook.strip() and check_language(h.hook, lang)[0]
                    and len(h.hook.split()) <= scripts.MAX_HOOK_WORDS):
                slots[h.slot_id]["hook"] = h.hook.strip()


@locked("next batch")
def generate_next_batch(app_id: str, size: Optional[int] = None, explore_ratio: Optional[float] = None,
                        use_llm_compliance: bool = True) -> dict[str, Any]:
    """Create generation n+1: allocate → hooks → scripts → compliance → tracking → select for render."""
    gen = current_generation(app_id)
    if gen == 0:
        raise ValueError("no Gen 1 yet — run `generate` first")
    nxt = gen + 1
    size = size or env_int("NEXT_BATCH_SIZE", env_int("GEN1_RENDER_COUNT", 12))
    explore_ratio = env_float("EXPLORE_RATIO", 0.3) if explore_ratio is None else explore_ratio
    ins = latest_insight(app_id)
    guidance = guidance_text(ins)

    from engine.research.market import context

    market = context(app_id)
    slots, allocation = plan_slots(app_id, size, explore_ratio, seed=nxt * 101)
    _assign_concepts(app_id, slots, nxt, guidance)
    _write_hooks(app_id, slots, nxt, guidance)
    slots = [s for s in slots if s.get("hook")]
    slots = dedupe(slots, group=lambda s: (s["language"], s["concept_id"], s["hook_type"]), threshold=0.8)

    rows = [Creative(app_id=app_id, concept_id=s["concept_id"], generation=nxt,
                     parent_creative_id=s.get("parent_creative_id"), language=s["language"], hook=s["hook"],
                     hook_type=s["hook_type"], format=s["format"] if s["format"] in formats() else "pov",
                     creator_style=s["creator_style"], persona=s.get("persona", ""), message=s["message"],
                     visual_mode=s.get("visual_mode", ""),
                     predicted_score=scripts.heuristic_score(s["hook"]), status="hook_only", origin=s["origin"],
                     market_evidence=market.evidence_ids({d: s[d] for d in ("format", "hook_type", "language",
                                                                             "creator_style")}) if market.available else [])
            for s in slots]
    save(*rows)
    sstats = scripts.write_scripts(app_id, rows, guidance=guidance)
    ok = [c for c in rows if c.status == "draft"]
    cstats = compliance.check_creatives(ok, brief_of(get_app(app_id)), use_llm=use_llm_compliance)
    tracking.assign(app_id, ok)
    for c in ok:
        if compliance.is_publishable(c):
            c.selected_for_render = True
            c.status = "approved"
    save(*ok)
    from engine.variations import assign_looks

    assign_looks(app_id, nxt, [c for c in ok if c.selected_for_render])

    allocation.update({"generation": nxt, "created": len(rows), "scripted": sstats["ok"], "compliance": cstats,
                       "selected_for_render": sum(1 for c in ok if c.selected_for_render),
                       "origin_counts": dict(Counter(c.origin for c in rows)), "guidance": guidance})
    out_dir = OUTPUTS_DIR / app_id / f"gen_{nxt}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "allocation.json").write_text(json.dumps(allocation, ensure_ascii=False, indent=2, default=float))
    if ins is not None:
        f = dict(ins.findings or {})
        f["next_allocation"] = json.loads(json.dumps(allocation, default=float))
        ins.findings = f
        save(ins)
    log.info(f"Gen {nxt}: {len(rows)} creatives ({allocation['origin_counts']}), {sstats['ok']} scripted")
    return {k: v for k, v in allocation.items() if k not in ("effects", "guidance")}
