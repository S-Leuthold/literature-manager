"""Metadata extraction orchestrator - tries multiple methods in priority order."""

from pathlib import Path
from typing import Dict

from literature_manager.config import Config
from literature_manager.extractors.doi import extract_with_doi
from literature_manager.extractors.llm import enhance_metadata_with_llm, extract_with_llm, extract_domain_attributes, generate_paper_summary
from literature_manager.extractors.pdf_metadata import extract_pdf_metadata
from literature_manager.extractors.text_parser import extract_text_from_pdf
from literature_manager.extractors.exceptions import (
    CorruptedPDFError,
    NetworkError,
    LLMError,
    ConfigurationError,
)


def extract_metadata(pdf_path: Path, config: Config) -> Dict:
    """
    Extract metadata from PDF using multiple methods in priority order.

    Tries in order:
    1. DOI + CrossRef lookup (confidence: 0.95) → then LLM enhancement
    2. PDF metadata fields (confidence: 0.70) → then LLM enhancement
    3. LLM parsing (confidence: 0.80) - full extraction

    After successful extraction, always enhances with LLM to generate:
    - Summary (4-10 word key finding)
    - Suggested topic (kebab-case)

    Args:
        pdf_path: Path to PDF file
        config: Configuration object

    Returns:
        Metadata dict with extracted information

    Raises:
        CorruptedPDFError: If PDF is corrupted (stops immediately)
        ConfigurationError: If API key missing (stops immediately)
    """
    preferred_methods = config.get("preferred_methods", ["doi_lookup", "pdf_metadata", "llm_parsing"])

    metadata = None
    errors_encountered = []  # Track what went wrong for logging

    for method in preferred_methods:
        try:
            if method == "doi_lookup":
                # Try DOI + CrossRef
                email = config.get("crossref_email")
                metadata = extract_with_doi(pdf_path, email)
                if metadata:
                    break
                else:
                    errors_encountered.append(f"{method}: not found")

            elif method == "pdf_metadata":
                # Try PDF metadata
                metadata = extract_pdf_metadata(pdf_path)
                if metadata:
                    break
                else:
                    errors_encountered.append(f"{method}: not found")

            elif method == "llm_parsing":
                # Try LLM extraction
                # First extract text
                pdf_text = extract_text_from_pdf(pdf_path)
                if pdf_text:
                    api_key = config.get("anthropic_api_key")
                    model = config.get("llm_model", "claude-haiku-4-5-20251001")
                    max_chars = config.get("llm_max_chars", 16000)
                    metadata = extract_with_llm(pdf_text, api_key, model, max_chars)
                    if metadata:
                        break
                    else:
                        errors_encountered.append(f"{method}: extraction returned empty")
                else:
                    errors_encountered.append(f"{method}: no text to parse")

        except (CorruptedPDFError, ConfigurationError):
            # Fatal errors - stop immediately and re-raise
            raise

        except NetworkError as e:
            # Network issue - log but continue to next method (transient)
            errors_encountered.append(f"{method}: network error - {e.message}")
            continue

        except LLMError as e:
            # LLM issue - log but continue to next method (API issue)
            errors_encountered.append(f"{method}: LLM error - {e.message}")
            continue

    # If all methods failed, return minimal metadata with error tracking
    if not metadata:
        metadata = {
            "title": "",
            "authors": [],
            "year": None,
            "abstract": None,
            "keywords": [],
            "extraction_method": "failed",
            "extraction_confidence": 0.0,
            "errors": errors_encountered,  # NEW: for logging
        }
    else:
        # SUCCESS! Now check if we need to extract abstract from PDF text
        # If DOI/PDF metadata succeeded but didn't get abstract, fallback to LLM text extraction
        if not metadata.get("abstract"):
            pdf_text = extract_text_from_pdf(pdf_path)
            if pdf_text:
                api_key = config.get("anthropic_api_key")
                model = config.get("llm_model", "claude-haiku-4-5-20251001")
                max_chars = config.get("llm_max_chars", 16000)
                # Use LLM to extract just the abstract from PDF text
                llm_metadata = extract_with_llm(pdf_text, api_key, model, max_chars)
                if llm_metadata and llm_metadata.get("abstract"):
                    # Keep the good metadata (title, authors, year from DOI) but add the abstract
                    metadata["abstract"] = llm_metadata["abstract"]
                    if not metadata.get("keywords") and llm_metadata.get("keywords"):
                        metadata["keywords"] = llm_metadata["keywords"]

        # Now enhance with LLM to get summary + topic suggestion
        # This runs for ALL successful extractions (DOI, PDF metadata, or LLM)
        api_key = config.get("anthropic_api_key")
        model = config.get("llm_model", "claude-haiku-4-5-20251001")

        if api_key:
            # Get existing topics from by-topic/ directory
            existing_topics = []
            by_topic_path = config.by_topic_path
            if by_topic_path.exists():
                existing_topics = [d.name for d in by_topic_path.iterdir() if d.is_dir() and not d.name.startswith('.')]

            metadata = enhance_metadata_with_llm(metadata, api_key, model, existing_topics=existing_topics)

            # Extract domain-specific attributes (study type, methods, fractions, etc.)
            if config.get("extract_domain_attributes", True):
                metadata = extract_domain_attributes(metadata, api_key, model)

            # Generate enhanced paper summary for Zotero notes
            if config.get("generate_paper_summary", True):
                metadata = generate_paper_summary(metadata, api_key, model)

    # Add original filename
    metadata["original_filename"] = pdf_path.name

    return metadata
