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
    pdf_text: str, api_key: str, model: str = "claude-haiku-4-20250514"
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
