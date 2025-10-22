"""Metadata extraction orchestrator - tries multiple methods in priority order."""

from pathlib import Path
from typing import Dict

from literature_manager.config import Config
from literature_manager.extractors.doi import extract_with_doi
from literature_manager.extractors.llm import extract_with_llm
from literature_manager.extractors.pdf_metadata import extract_pdf_metadata
from literature_manager.extractors.text_parser import extract_text_from_pdf


def extract_metadata(pdf_path: Path, config: Config) -> Dict:
    """
    Extract metadata from PDF using multiple methods in priority order.

    Tries in order:
    1. DOI + CrossRef lookup (confidence: 0.95)
    2. PDF metadata fields (confidence: 0.70)
    3. LLM parsing (confidence: 0.80)

    Args:
        pdf_path: Path to PDF file
        config: Configuration object

    Returns:
        Metadata dict with extracted information
    """
    preferred_methods = config.get("preferred_methods", ["doi_lookup", "pdf_metadata", "llm_parsing"])

    metadata = None

    for method in preferred_methods:
        if method == "doi_lookup":
            # Try DOI + CrossRef
            email = config.get("crossref_email")
            metadata = extract_with_doi(pdf_path, email)
            if metadata:
                break

        elif method == "pdf_metadata":
            # Try PDF metadata
            metadata = extract_pdf_metadata(pdf_path)
            if metadata:
                break

        elif method == "llm_parsing":
            # Try LLM extraction
            # First extract text
            pdf_text = extract_text_from_pdf(pdf_path)
            if pdf_text:
                api_key = config.get("anthropic_api_key")
                model = config.get("llm_model", "claude-haiku-4-20250514")
                metadata = extract_with_llm(pdf_text, api_key, model)
                if metadata:
                    break

    # If all methods failed, return minimal metadata
    if not metadata:
        metadata = {
            "title": "",
            "authors": [],
            "year": None,
            "abstract": None,
            "keywords": [],
            "extraction_method": "failed",
            "extraction_confidence": 0.0,
        }

    # Add original filename
    metadata["original_filename"] = pdf_path.name

    return metadata
