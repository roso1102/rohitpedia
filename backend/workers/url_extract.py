from __future__ import annotations

import os
from typing import Any

import httpx
import trafilatura


async def _extract_with_firecrawl(url: str) -> str | None:
    api_key = os.getenv("FIRECRAWL_API_KEY", "").strip()
    if not api_key:
        return None

    try:
        from firecrawl import FirecrawlApp
    except Exception:
        return None

    app = FirecrawlApp(api_key=api_key)
    result = app.scrape_url(url=url, formats=["markdown"])
    if not result:
        return None

    markdown = result.get("markdown") if isinstance(result, dict) else None
    if markdown and len(markdown.strip()) > 200:
        return markdown.strip()
    return None


async def _extract_with_jina(url: str) -> str | None:
    jina_url = f"https://r.jina.ai/{url}"
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(jina_url)
        if response.status_code != 200:
            return None
        content = response.text.strip()
        if len(content) > 200:
            return content
        return None


async def _extract_with_trafilatura(url: str) -> str | None:
    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        return None
    extracted = trafilatura.extract(downloaded)
    if extracted and len(extracted.strip()) > 200:
        return extracted.strip()
    return None


async def _extract_with_playwright(url: str) -> str | None:
    try:
        from playwright.async_api import async_playwright
    except Exception:
        return None

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            content = (await page.locator("body").inner_text()).strip()
            await browser.close()
            if len(content) > 200:
                return content
            return None
    except Exception:
        return None


async def extract_url_content(url: str) -> tuple[str, str]:
    if not url:
        return ("", "missing_url")

    firecrawl_result = await _extract_with_firecrawl(url)
    if firecrawl_result:
        return (firecrawl_result, "firecrawl")

    jina_result = await _extract_with_jina(url)
    if jina_result:
        return (jina_result, "jina")

    trafilatura_result = await _extract_with_trafilatura(url)
    if trafilatura_result:
        return (trafilatura_result, "trafilatura")

    playwright_result = await _extract_with_playwright(url)
    if playwright_result:
        return (playwright_result, "playwright")

    return (url, "fallback_url_only")
