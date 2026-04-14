"""Verify 4.7 facet extraction with a turmeric-focused article sample."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workers.absorb import _extract_facets, _get_facets_provider


TURMERIC_SAMPLE = """
# Turmeric
Turmeric is a bright yellow spice used widely in Indian cuisine.
Curcumin is associated with anti-inflammatory properties in many summaries.
It is common in curries and golden milk.
""".strip()


async def main() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    provider = _get_facets_provider()
    facets, err = await _extract_facets(provider, body_md=TURMERIC_SAMPLE)
    print("error:", err)
    print("facets:", facets)
    if err or not facets:
        raise SystemExit(1)

    health = set(facets.get("health", []))
    cuisine = set(facets.get("cuisine", []))
    colors = set(facets.get("colors", []))
    expected_ok = (
        "anti-inflammatory" in health
        and "indian" in cuisine
        and "yellow" in colors
    )
    print("expected_triplet_present:", expected_ok)
    if not expected_ok:
        raise SystemExit(2)


if __name__ == "__main__":
    asyncio.run(main())
