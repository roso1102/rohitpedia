# SUBJECT GUIDE: LLM Usage, Prompting, and Cost Management
## Rohitpedia Engineering Standards

---

## Core rule: LLMs are the last resort

Before calling an LLM, verify there is no deterministic alternative:

| Task | Don't use LLM | Use instead |
|---|---|---|
| Wikilink extraction | LLM | AST parser (markdown-it-py) |
| Conflict detection (first pass) | LLM | SQL: facet vs avoid set intersection |
| Intent detection (query vs capture) | LLM | Regex pattern matching |
| Backlink writing | LLM | SQL INSERT after AST parsing |
| Duplicate detection | LLM | pgvector cosine similarity |
| Decay computation | LLM | Math formula |

LLMs are appropriate for: synthesis, creative writing, nuanced reasoning, pattern discovery, explanation generation.

---

## LLM touch points in Rohitpedia

| Task | Model | Avg tokens in | Avg tokens out | Frequency |
|---|---|---|---|---|
| Absorb synthesis | Gemini Flash 2.5 | ~6,000 | ~1,000 | Per capture |
| Facet extraction | Phi-3 mini (local) | ~1,000 | ~200 | Per absorb |
| Image description | Gemini Flash (vision) | image + 50 | ~200 | Per image |
| Tunnel rationale | Gemini Flash 2.5 | ~15,000 | ~2,000 | Nightly (8 pairs) |
| Diff synthesis | Phi-3 mini or Flash 8B | ~4,000 | ~500 | Nightly per context |
| Telegram query reply | Gemini Flash 8B | ~3,000 | ~300 | Per query |
| Era summary | Gemini Pro | ~20,000 | ~2,000 | Once per context close |
| Conflict detection | Gemini Flash | ~3,000 | ~200 | Nightly if triggered |

---

## Provider abstraction (mandatory pattern)

```python
# backend/llm/provider.py
from abc import ABC, abstractmethod
from pydantic import BaseModel

class LLMUsage(BaseModel):
    input_tokens: int
    output_tokens: int

class LLMResponse(BaseModel):
    content: str
    usage: LLMUsage

class LLMProvider(ABC):
    @abstractmethod
    async def complete(
        self,
        prompt: str,
        system_prompt: str | None = None,
        max_tokens: int = 1000,
        response_format: dict | None = None
    ) -> LLMResponse:
        ...

    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        ...

# backend/llm/gemini.py
import google.generativeai as genai

class GeminiProvider(LLMProvider):
    def __init__(self, model: str = "gemini-2.5-flash"):
        self.model = genai.GenerativeModel(model)

    async def complete(self, prompt, system_prompt=None,
                       max_tokens=1000, response_format=None) -> LLMResponse:
        response = await self.model.generate_content_async(
            prompt,
            generation_config=genai.GenerationConfig(
                max_output_tokens=max_tokens,
                response_mime_type="application/json" if response_format else "text/plain"
            )
        )
        return LLMResponse(
            content=response.text,
            usage=LLMUsage(
                input_tokens=response.usage_metadata.prompt_token_count,
                output_tokens=response.usage_metadata.candidates_token_count
            )
        )

# Factory
def get_provider(task: str) -> LLMProvider:
    providers = {
        "absorb": GeminiProvider("gemini-2.5-flash"),
        "facets": OllamaProvider("phi3:mini"),
        "rationale": GeminiProvider("gemini-2.5-flash"),
        "diff": OllamaProvider("phi3:mini"),
        "query": GeminiProvider("gemini-2.5-flash-8b"),
        "era_summary": GeminiProvider("gemini-pro"),
    }
    return providers[task]
```

---

## Prompt engineering standards

### Absorb prompt structure
```python
ABSORB_SYSTEM_PROMPT = """
You are a personal wiki curator. Your job is to synthesise a raw capture into a structured wiki article.

RULES:
1. Write in clear, concise prose. No bullet lists in the article body.
2. Extract [[wikilinks]] ONLY to slugs in the CANDIDATE SLUG LIST provided. Never invent slugs.
3. Use ## and ### headers to structure the article.
4. Extract facets as valid JSON matching the FACET SCHEMA.
5. If a relevant article exists in CONTEXT ARTICLES, update it rather than creating new.
6. Preserve the user's own words where they add meaning.

FACET SCHEMA:
{
  "category": [],      // e.g. ["spice", "ingredient"]
  "themes": [],        // e.g. ["health", "cooking"]
  "colors": [],        // e.g. ["yellow", "golden"]
  "health": [],        // e.g. ["anti-inflammatory"]
  "cuisine": [],       // e.g. ["Indian", "Asian"]
  "style": [],         // aesthetic descriptors
  "sentiment": ""      // "positive" | "neutral" | "negative"
}

OUTPUT FORMAT (JSON):
{
  "slug": "lowercase-hyphenated",
  "title": "Article Title",
  "body_md": "Full markdown content with [[wikilinks]]",
  "facets": { <facet schema above> }
}
"""
# This prompt is cached via Gemini context caching.
# Do not change it frequently — cache invalidates on change.

def build_absorb_prompt(entry: RawEntry, context_articles: list[Article],
                        all_slugs: list[str]) -> str:
    return f"""
RAW CAPTURE:
{entry.body}

CONTEXT: {entry.context or 'general'}

CONTEXT ARTICLES (may update these):
{format_context_articles(context_articles)}

CANDIDATE SLUG LIST (ONLY link to these):
{', '.join(all_slugs[:50])}

Synthesise the raw capture into a wiki article following the system rules.
"""
```

### Facet extraction prompt (Phi-3 mini)
```python
FACET_PROMPT = """
Extract structured facets from the article text below.
Return ONLY valid JSON matching the schema. No explanation.

Schema:
{"category": [], "themes": [], "colors": [], "health": [], "cuisine": [], "style": [], "sentiment": ""}

Mark uncertain values with "?" suffix, e.g. "North Indian?".

Article:
{article_body}
"""
```

### Validation after every LLM call
```python
import json
from jsonschema import validate, ValidationError

ABSORB_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["slug", "title", "body_md", "facets"],
    "properties": {
        "slug": {"type": "string", "pattern": "^[a-z0-9-]+$"},
        "title": {"type": "string", "minLength": 1},
        "body_md": {"type": "string", "minLength": 10},
        "facets": {"type": "object"}
    }
}

def parse_and_validate(raw_response: str) -> dict:
    try:
        # Strip markdown code fences if present
        clean = raw_response.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        parsed = json.loads(clean)
        validate(parsed, ABSORB_OUTPUT_SCHEMA)
        return parsed
    except (json.JSONDecodeError, ValidationError) as e:
        logger.error("llm_parse_failed", error=str(e), raw=raw_response[:200])
        raise AbsorbError(f"LLM returned invalid response: {e}")
```

---

## Error handling and retry logic

```python
import asyncio
from typing import TypeVar, Callable, Awaitable

T = TypeVar("T")

async def with_retry(
    fn: Callable[[], Awaitable[T]],
    max_attempts: int = 3,
    base_delay: float = 1.0,
    task_name: str = "llm_call"
) -> T:
    """Retry with exponential backoff. Logs all failures."""
    for attempt in range(max_attempts):
        try:
            return await fn()
        except Exception as e:
            if attempt == max_attempts - 1:
                logger.error(f"{task_name}_exhausted", attempts=max_attempts, error=str(e))
                raise
            delay = base_delay * (2 ** attempt)
            logger.warning(f"{task_name}_retry", attempt=attempt + 1, delay=delay, error=str(e))
            await asyncio.sleep(delay)
```

---

## Gemini context caching

```python
# Cache the absorb system prompt to save 70-80% on repeated calls
import google.generativeai as genai
from datetime import timedelta

async def get_cached_absorb_model():
    cache = genai.caching.CachedContent.create(
        model="gemini-2.5-flash",
        contents=[ABSORB_SYSTEM_PROMPT],
        ttl=timedelta(hours=23),  # refresh just under 24 hours
    )
    return genai.GenerativeModel.from_cached_content(cache)

# Cache at startup, reuse for the day
# Store cache_name in app state, recreate if expired
```

---

## Cost tracking

```python
# Every LLM call logs to llm_usage table
async def log_llm_usage(
    user_id: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    duration_ms: int,
    task_type: str,
    success: bool,
    db: AsyncSession
):
    await db.execute(
        insert(LlmUsage).values(
            user_id=user_id,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            duration_ms=duration_ms,
            task_type=task_type,
            success=success,
        )
    )

# Query total cost per user per month
async def get_monthly_cost(user_id: str, db: AsyncSession) -> float:
    GEMINI_FLASH_COST_PER_1M_IN = 0.075   # USD
    GEMINI_FLASH_COST_PER_1M_OUT = 0.30   # USD
    result = await db.execute(
        text("""
            SELECT
                SUM(tokens_in) / 1000000.0 * :cost_in as input_cost,
                SUM(tokens_out) / 1000000.0 * :cost_out as output_cost
            FROM llm_usage
            WHERE user_id = current_setting('app.current_tenant')::uuid
              AND created_at >= date_trunc('month', now())
              AND model LIKE 'gemini%flash%'
        """),
        {"cost_in": GEMINI_FLASH_COST_PER_1M_IN,
         "cost_out": GEMINI_FLASH_COST_PER_1M_OUT}
    )
    row = result.one()
    return (row.input_cost or 0) + (row.output_cost or 0)
```

---

## Common LLM mistakes

```python
# MISTAKE 1: No max_tokens set
response = await model.generate_content(prompt)
# FIX: Always set max_tokens explicitly
response = await model.generate_content(
    prompt,
    generation_config=GenerationConfig(max_output_tokens=1000)
)

# MISTAKE 2: Feeding full article corpus to LLM
all_articles = await get_all_articles(user_id)  # could be 1000 articles
prompt = f"Given these articles: {all_articles}\nFind connections."
# FIX: Use vector search to retrieve top-8 relevant articles only

# MISTAKE 3: No output validation
result_text = response.text
result_dict = json.loads(result_text)  # crashes if invalid JSON
# FIX: Use parse_and_validate() with schema validation + fallback

# MISTAKE 4: Logging user content in prompts
logger.debug(f"Prompt: {prompt}")  # logs user's private content
# FIX: Log only metadata, never prompt content

# MISTAKE 5: Hallucinated wikilinks
# LLM invents [[non-existent-slug]] links
# FIX: Include explicit instruction + slug allowlist in prompt
```
