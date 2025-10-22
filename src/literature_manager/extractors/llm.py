"""LLM-based metadata extraction using Claude Haiku."""

import json
from typing import Dict, Optional

from anthropic import Anthropic

from literature_manager.extractors.text_parser import truncate_text_for_llm
from literature_manager.utils import normalize_whitespace


EXTRACTION_PROMPT = """You are extracting bibliographic metadata from a scientific paper.

Extract the following information from the text below:
1. **title**: Full title of the paper
2. **authors**: List of author names (format as "Last, F." where F is first initial)
3. **year**: Publication year (4-digit number)
4. **abstract**: Abstract or summary of the paper (if available)
5. **keywords**: List of scientific keywords or key concepts from the paper
6. **short_title**: A shortened version of the title (5-8 words, title case, natural break point)
7. **suggested_topic**: A kebab-case topic name this paper belongs to (e.g., "soil-carbon", "fractionation-methods", "spectroscopy")

Return your response as valid JSON with these exact keys:
{
    "title": "...",
    "authors": ["Last1, F.", "Last2, F.", ...],
    "year": 2024,
    "abstract": "...",
    "keywords": ["keyword1", "keyword2", ...],
    "short_title": "...",
    "suggested_topic": "topic-name"
}

If any field cannot be determined, use null for strings, [] for lists, or the current year for year.

PAPER TEXT:
---
%TEXT%
---

Return ONLY the JSON object, no other text."""


def extract_with_llm(
    pdf_text: str, api_key: str, model: str = "claude-3-5-haiku-20241022"
) -> Optional[Dict]:
    """
    Extract metadata using Claude LLM.

    Args:
        pdf_text: Extracted text from PDF
        api_key: Anthropic API key
        model: Claude model to use

    Returns:
        Metadata dict if successful, None otherwise
    """
    if not pdf_text or not pdf_text.strip():
        return None

    try:
        # Truncate text to fit within token limits
        truncated_text = truncate_text_for_llm(pdf_text)

        # Create prompt
        prompt = EXTRACTION_PROMPT.replace("%TEXT%", truncated_text)

        # Call Claude API
        client = Anthropic(api_key=api_key)

        message = client.messages.create(
            model=model, max_tokens=2000, temperature=0, messages=[{"role": "user", "content": prompt}]
        )

        # Extract response
        response_text = message.content[0].text

        # Parse JSON response
        try:
            metadata = json.loads(response_text)
        except json.JSONDecodeError:
            # Try to extract JSON from response if surrounded by other text
            import re

            json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
            if json_match:
                metadata = json.loads(json_match.group(0))
            else:
                return None

        # Validate and normalize
        result = {
            "title": normalize_whitespace(metadata.get("title", "")) if metadata.get("title") else "",
            "authors": metadata.get("authors", []),
            "year": metadata.get("year"),
            "abstract": normalize_whitespace(metadata.get("abstract", ""))
            if metadata.get("abstract")
            else None,
            "keywords": metadata.get("keywords", []),
            "short_title": metadata.get("short_title", ""),
            "suggested_topic": metadata.get("suggested_topic", ""),
            "extraction_method": "llm_parsing",
            "extraction_confidence": 0.80,
        }

        # Must have title
        if not result["title"]:
            return None

        # Lower confidence if missing key fields
        if not result["authors"]:
            result["extraction_confidence"] = 0.70
        if not result["year"]:
            from datetime import datetime

            result["year"] = datetime.now().year

        return result

    except Exception as e:
        print(f"LLM extraction error: {e}")
        return None


ENHANCEMENT_PROMPT = """You are analyzing scientific paper metadata to create a concise summary.

Given this paper's metadata:
- **Title**: %TITLE%
- **Abstract**: %ABSTRACT%
- **Keywords**: %KEYWORDS%

Provide:
1. **summary**: A 4-10 word summary of the KEY FINDING or MAIN CONCEPT.
   NOT just a shortened title - distill what the paper actually discovered or proposes.
   Focus on the scientific contribution, not methodology.

2. **suggested_topic**: A kebab-case topic name (e.g., "soil-carbon-saturation", "litter-decomposition")

Examples of GOOD summaries:
- "Litter Quality Controls Carbon Saturation"
- "Tillage Reduces Labile Carbon Pools"
- "Diverse Rotations Increase System Resilience"
- "Nitrogen Availability Limits Decomposition Rates"

Examples of BAD summaries (too generic or just shortened title):
- "A Study of Soil Carbon" (too vague)
- "Effects of Management on Organic" (truncated, not informative)

Return ONLY valid JSON:
{
    "summary": "...",
    "suggested_topic": "..."
}"""


def enhance_metadata_with_llm(
    metadata: Dict, api_key: str, model: str = "claude-3-5-haiku-20241022", retry: bool = True
) -> Dict:
    """
    Enhance metadata with LLM-generated summary and topic suggestion.

    Takes existing metadata (from DOI, PDF metadata, etc.) and uses LLM to:
    - Generate a concise summary (4-10 words) of the key finding
    - Suggest a topic in kebab-case

    Args:
        metadata: Metadata dict with at minimum 'title', optionally 'abstract' and 'keywords'
        api_key: Anthropic API key
        model: Claude model to use
        retry: Whether to retry once on failure

    Returns:
        Enhanced metadata dict with 'summary' and 'suggested_topic' fields added
    """
    # Build prompt
    title = metadata.get("title", "")
    abstract = metadata.get("abstract", "")
    keywords = metadata.get("keywords", [])

    if not title:
        # Can't enhance without at least a title
        return metadata

    # Build text for LLM
    keywords_str = ", ".join(keywords) if keywords else "None provided"

    if not abstract:
        abstract = "Not available - analyze title and keywords only"

    # Truncate abstract if too long
    if len(abstract) > 4000:
        abstract = abstract[:4000] + "..."

    prompt = ENHANCEMENT_PROMPT.replace("%TITLE%", title)
    prompt = prompt.replace("%ABSTRACT%", abstract)
    prompt = prompt.replace("%KEYWORDS%", keywords_str)

    try:
        # Call Claude API
        client = Anthropic(api_key=api_key)

        message = client.messages.create(
            model=model, max_tokens=200, temperature=0, messages=[{"role": "user", "content": prompt}]
        )

        # Extract response
        response_text = message.content[0].text

        # Parse JSON response
        try:
            result = json.loads(response_text)
        except json.JSONDecodeError:
            # Try to extract JSON from response
            import re

            json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group(0))
            else:
                raise ValueError("Could not parse JSON from LLM response")

        # Validate response
        summary = result.get("summary", "")
        suggested_topic = result.get("suggested_topic", "")

        if summary:
            # Validate word count (flexible 4-10 words)
            word_count = len(summary.split())
            if 4 <= word_count <= 10:
                metadata["summary"] = summary
            else:
                # Use it anyway but log warning
                print(f"Warning: Summary has {word_count} words (expected 4-10): {summary}")
                metadata["summary"] = summary

        if suggested_topic:
            metadata["suggested_topic"] = suggested_topic

        return metadata

    except Exception as e:
        print(f"LLM enhancement error: {e}")

        # Retry once if requested
        if retry:
            print("Retrying LLM enhancement...")
            return enhance_metadata_with_llm(metadata, api_key, model, retry=False)

        # Fallback: return metadata unchanged
        return metadata
