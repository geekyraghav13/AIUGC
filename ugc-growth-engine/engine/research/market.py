"""Market context used by the generation modules (D8.6): priors, evidence ids and guidance text."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

import pandas as pd

from engine.research import cfg
from engine.research.patterns import TAG_MAP

PRIOR_DIMS = ["format", "hook_type", "language", "creator_style", "visual_mode"]  # our tags with market equivalents


@dataclass
class MarketContext:
    playbook: dict[str, Any] = field(default_factory=dict)
    patterns: pd.DataFrame = field(default_factory=pd.DataFrame)
    run_id: Optional[int] = None

    @property
    def available(self) -> bool:
        return bool(self.playbook) and not self.patterns.empty

    def rows(self, our_dim: str) -> pd.DataFrame:
        """Pattern rows for one of our tag dimensions (maps creator_style ← closest_creator_style)."""
        if self.patterns.empty:
            return self.patterns
        if our_dim == "visual_mode":
            return self._mode_rows()
        mdim = next((m for m, o in TAG_MAP.items() if o == our_dim), our_dim)
        return self.patterns[self.patterns["dimension"] == mdim]

    def _mode_rows(self) -> pd.DataFrame:
        """Market production styles folded into our visual modes (talking_head → ai_character, skit → character …)."""
        from engine.research.patterns import min_videos
        from engine.settings import taxonomy

        ps = self.patterns[self.patterns["dimension"] == "production_style"]
        ps = ps[ps["value"].astype(str) != "other"] if not ps.empty else ps   # unknown style ≠ evidence
        if ps.empty:
            return ps
        m2v = taxonomy()["market_to_visual_mode"]
        g = ps.assign(mode=ps["value"].map(lambda v: m2v.get(str(v), "faceless")))
        rows = []
        for mode, sub in g.groupby("mode"):
            vids = int(sub["videos"].sum())
            rows.append({"id": int(sub.sort_values("weighted_share", ascending=False).iloc[0]["id"]),
                         "dimension": "visual_mode", "value": mode, "videos": vids, "share": float(sub["share"].sum()),
                         "weighted_share": float(sub["weighted_share"].sum()),
                         "avg_outlier": float((sub["avg_outlier"] * sub["videos"]).sum() / max(1, vids)),
                         "lift": float((sub["lift"] * sub["videos"]).sum() / max(1, vids)),
                         "signal": "pattern" if vids >= min_videos() else "weak signal"})
        return pd.DataFrame(rows)

    def edge(self, our_dim: str, value: str) -> Optional[float]:
        """log over-representation of a proven value among the over-performing sample:
        log(weighted_share × number of values seen). 0 = what an even split would give; None = no proven signal.
        (The sample is already the top outliers, so being common in it is the evidence that it works.)"""
        r = self.rows(our_dim)
        if r.empty:
            return None
        m = r[(r["value"] == value) & (r["signal"] == "pattern")]
        if m.empty:
            return None
        return float(math.log(max(1e-3, float(m.iloc[0]["weighted_share"]) * len(r))))

    def best(self, our_dim: str) -> Optional[str]:
        """Proven value with the highest weighted share (= highest edge)."""
        r = self.rows(our_dim)
        r = r[(r["signal"] == "pattern") & (r["value"] != "other")] if not r.empty else r
        return r.sort_values("weighted_share", ascending=False).iloc[0]["value"] if not r.empty else None

    def prior_score(self, tags: dict[str, str]) -> float:
        """0–100 market prior for a creative's tags (50 = no market signal)."""
        vals = []
        for d in PRIOR_DIMS:
            e = self.edge(d, tags.get(d, ""))
            vals.append(50.0 if e is None else 50 + 50 * math.tanh(e))
        return round(sum(vals) / len(vals), 1)

    def evidence_ids(self, tags: dict[str, str]) -> list[int]:
        """Ids of proven, above-average market patterns that match these tags."""
        out = []
        for d in PRIOR_DIMS:
            r = self.rows(d)
            if r.empty:
                continue
            e = self.edge(d, tags.get(d, ""))
            if e is not None and e > 0:
                out += [int(i) for i in r[r["value"] == tags.get(d, "")]["id"]]
        return out

    def proven(self) -> list[dict[str, Any]]:
        """Proven patterns concepts may build on: recipes + topic-angle clusters + proven formats."""
        out = []
        for i, r in enumerate(self.playbook.get("recipes", [])):
            out.append({"kind": "recipe", "ids": r.get("pattern_ids", []), "format": r["format"],
                        "hook_type": r["hook_type"], "language": r["language"], "name": r["name"]})
        if not self.patterns.empty:
            t = self.patterns[(self.patterns["dimension"] == "topic_angle") & (self.patterns["signal"] == "pattern")]
            for r in t.sort_values("weighted_share", ascending=False).head(5).itertuples():
                out.append({"kind": "topic_angle", "ids": [int(r.id)], "topic_angle": r.value})
            f = self.rows("format")
            for r in f[(f["signal"] == "pattern") & (f["value"] != "other")].itertuples():
                if (self.edge("format", r.value) or 0) > 0:
                    out.append({"kind": "format", "ids": [int(r.id)], "format": r.value})
        return out

    def optimizer_priors(self, s_bar: float) -> dict[tuple[str, str], tuple[float, float]]:
        """{(dim, value): (prior mean of log-effect, prior precision)} capped at market_prior_strength
        pseudo-observations of a typical creative (`s_bar` successes each), so our data overrides it quickly."""
        strength = float(cfg()["market_prior_strength"])
        out = {}
        for d in PRIOR_DIMS:
            r = self.rows(d)
            if r.empty:
                continue
            for row in r[r["signal"] == "pattern"].itertuples():
                mu = max(-0.5, min(0.5, 0.5 * (self.edge(d, row.value) or 0.0)))
                out[(d, row.value)] = (mu, strength * max(1.0, s_bar))
        return out

    def guidance(self) -> str:
        """Prompt guidance: recipes + hook templates (abstract patterns — never copy) + language emphasis."""
        if not self.available:
            return ""
        pb = self.playbook
        lines = ["MARKET PLAYBOOK (what is over-performing on Indian YouTube Shorts right now — use as guidance, never copy):"]
        for r in pb.get("recipes", []):
            lines.append(f"- Recipe “{r['name']}”: format={r['format']}, hook_type={r['hook_type']}, "
                         f"language={r['language']}, style={r['creator_style']} ({r['evidence']})")
        if pb.get("hook_templates"):
            lines.append("- Hook templates (fill the blanks with our app's scenario; do not reuse other creators' lines): "
                         + " | ".join(pb["hook_templates"]))
        lang = self.rows("language")
        if not lang.empty:
            lines.append("- Language emphasis in the market: " + ", ".join(
                f"{r.value} {r.weighted_share * 100:.0f}% weighted share" for r in lang.head(4).itertuples()))
        if pb.get("avoid"):
            lines.append("- Under-performing in the market: " + "; ".join(pb["avoid"][:3]))
        return "\n".join(lines)


def context(app_id: str) -> MarketContext:
    """Latest market context for an app (empty context if no research yet)."""
    from engine.research.run import latest_playbook, market_patterns

    pb = latest_playbook(app_id)
    if pb is None:
        return MarketContext()
    return MarketContext(playbook=pb.playbook or {}, patterns=market_patterns(app_id), run_id=pb.run_id)
