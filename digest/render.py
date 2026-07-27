"""Renders the daily HTML email digest from classified+deduped items."""

from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATE_DIR = Path(__file__).resolve().parent

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
)


def render_digest(
    candidates: list,
    items_by_candidate: dict,
    velocity: dict,
    rejected_counts: dict,
    enrichment_lines: list = None,
) -> str:
    """enrichment_lines is pipeline.enrich.Enricher.summary_lines() -- how
    many snippet-only items got real article text this run. Reported in the
    email because a broken full-text path shows up as a *shorter* digest,
    which is indistinguishable from a quiet news day unless we say so."""
    enrichment_lines = enrichment_lines or []
    template = _env.get_template("template.html.jinja")
    return template.render(
        candidates=candidates,
        items_by_candidate=items_by_candidate,
        velocity=velocity,
        rejected_counts=rejected_counts,
        enrichment_lines=enrichment_lines,
        enrichment_degraded=any(line.lstrip().startswith("[!]") for line in enrichment_lines),
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
