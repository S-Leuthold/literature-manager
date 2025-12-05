"""LLM-based metadata extraction using Claude Haiku."""

import json
from typing import Dict, List, Optional

from anthropic import Anthropic

from literature_manager.extractors.text_parser import truncate_text_for_llm
from literature_manager.utils import normalize_whitespace
from literature_manager.extractors.exceptions import LLMError, ConfigurationError


EXTRACTION_PROMPT = """You are a scientific literature specialist extracting bibliographic metadata from paper text.

<task>
Extract bibliographic information and create a concise summary from the paper text below.
</task>

<paper_text>
%TEXT%
</paper_text>

<instructions>
Extract the following fields:
- title: Full paper title
- authors: List in format "Last, F." (first initial only)
- year: 4-digit publication year
- abstract: Full abstract text if present
- keywords: List of key scientific terms and concepts
- short_title: 4-6 word summary of KEY FINDING (use active voice: "X Controls Y", "Treatment Increases Z")
- suggested_topic: BROAD soil science category in kebab-case (examples: soil-carbon, soil-organic-matter, soil-microbiology, nutrient-cycling)

For short_title: Focus on what was DISCOVERED, not what was studied. Use strong verbs.
For suggested_topic: Think soil science library shelves. Default to soil-related topics when possible.

If a field cannot be determined:
- Use null for title/abstract
- Use [] for authors/keywords
- Use current year for year
- Use null for short_title/suggested_topic
</instructions>

<output_format>
Return ONLY valid JSON (no other text):
{
    "title": "...",
    "authors": ["Last1, F.", "Last2, F.", ...],
    "year": 2024,
    "abstract": "...",
    "keywords": ["keyword1", "keyword2", ...],
    "short_title": "Finding-Focused Summary",
    "suggested_topic": "broad-category"
}
</output_format>"""


def extract_with_llm(
    pdf_text: str, api_key: str, model: str = "claude-haiku-4-5-20251001",
    max_chars: int = 16000
) -> Optional[Dict]:
    """
    Extract metadata using Claude LLM.

    Args:
        pdf_text: Extracted text from PDF
        api_key: Anthropic API key
        model: Claude model to use
        max_chars: Maximum characters for LLM context (default from config)

    Returns:
        Metadata dict if successful, None if no text or no title found

    Raises:
        ConfigurationError: If API key is missing
        LLMError: If API call fails or returns invalid JSON
    """
    if not api_key:
        raise ConfigurationError(
            "Anthropic API key not configured",
            method="llm_parsing"
        )

    if not pdf_text or not pdf_text.strip():
        return None

    try:
        # Truncate text to fit within token limits
        truncated_text = truncate_text_for_llm(pdf_text, max_chars=max_chars)

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
                try:
                    metadata = json.loads(json_match.group(0))
                except json.JSONDecodeError:
                    raise LLMError(
                        "LLM returned invalid JSON",
                        api_error=response_text[:200],
                        method="llm_parsing"
                    )
            else:
                raise LLMError(
                    "LLM returned invalid JSON",
                    api_error=response_text[:200],
                    method="llm_parsing"
                )

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

    except (LLMError, ConfigurationError):
        # Re-raise our custom exceptions
        raise
    except anthropic.APIConnectionError as e:
        raise LLMError(
            f"Anthropic API connection failed: {e}",
            api_error=str(e),
            method="llm_parsing"
        )
    except anthropic.RateLimitError as e:
        raise LLMError(
            f"Anthropic rate limit exceeded: {e}",
            api_error=str(e),
            method="llm_parsing"
        )
    except anthropic.APIStatusError as e:
        if e.status_code == 401:
            raise ConfigurationError(
                f"Invalid Anthropic API key: {e}",
                method="llm_parsing"
            )
        else:
            raise LLMError(
                f"Anthropic API error ({e.status_code}): {e}",
                api_error=str(e),
                method="llm_parsing"
            )
    except Exception as e:
        raise LLMError(
            f"LLM extraction failed: {type(e).__name__}: {e}",
            api_error=str(e),
            method="llm_parsing"
        )


ENHANCEMENT_PROMPT = """You are a scientific literature specialist categorizing soil science papers using a FIXED TAXONOMY.

<task>
1. Create a 4-6 word summary of the KEY FINDING
2. Select topic(s) from the ALLOWED TOPICS list below (see policy for count)
</task>

<paper_metadata>
<title>%TITLE%</title>
<abstract>%ABSTRACT%</abstract>
<keywords>%KEYWORDS%</keywords>
</paper_metadata>

<summary_instructions>
Create a 6-8 word summary that states the MAIN FINDING (what was discovered).

RULES:
1. State the finding as a declarative sentence (6-8 words)
2. Use active verbs (controls, increases, limits, determines, reveals, drives, protects, reduces)
3. Front-load the most distinctive information in first 3 words
4. Be specific enough to distinguish from similar papers
5. Include numbers or context if it adds clarity
6. Must be exactly 6-8 words in Title Case

GOOD EXAMPLES:
- "Mineral Sorption Limits Long-Term Soil Carbon Storage" (7 words)
- "No-Till Management Increases Soil Aggregate Stability" (6 words)
- "Soil pH Determines Phosphorus Availability Across Regions" (7 words)
- "Severe Drought Reduces Microbial Biomass By 60%" (7 words)
- "Clay Minerals Protect Carbon Through Surface Adsorption" (7 words)

BAD EXAMPLES:
- "Sorption Limits Carbon" (too short, not specific)
- "How Soil Carbon Is Protected" (question format)
- "Tillage Impacts Soil Carbon Storage" (vague verb "impacts")
- "Study Shows NIRS Predicts Properties Variably" (adverb + meta language)

EXCEPTIONS (use method-focused instead):
- Methods papers: "NIRS Successfully Predicts Soil Phosphorus Availability"
- Reviews: "Meta-Analysis Reveals Variable Biochar Benefits Across Studies"
- Multiple findings: "Temperature And Moisture Jointly Control Soil Respiration"
</summary_instructions>

<topic_selection_process>
STEP 1: Identify Primary Research Contribution
Ask: "What new knowledge does this paper generate?"
- Focus on the FINDINGS, not the study design
- Ignore contextual variables (temperature, moisture, site characteristics)
- Distinguish between what was STUDIED vs what was USED as a tool

STEP 2: Determine Topic Type
Is this contribution about:
- A substantive soil science topic? → Select that topic, proceed to STEP 3
- A methodological innovation? → Select method topic, proceed to STEP 3
- Equally about both? → Select both (rare, see Topic Count Policy below)
- No clear fit? → Go to STEP 5

STEP 3: Check for Method Topic (Secondary)
Add a method topic from "Analytical Methods" category ONLY IF:
✓ Method is named in the title, OR
✓ Method development/validation is a stated objective, OR
✓ Paper presents novel methodological insights (not just uses standard protocol)

Do NOT add method topic if:
✗ Method is standard analytical procedure used to generate data
✗ Method only mentioned in materials/methods section
✗ Multiple routine methods used (e.g., standard pH, texture analysis)

Exception: If paper is ONLY about comparing/validating methods:
- Method topic is PRIMARY (only topic)
- Do not add substantive topic unless findings have clear implications

STEP 4: Apply Topic Selection Rules
Topic Count Policy:
- DEFAULT: Select 2 topics when paper has multiple clear research dimensions (~60% of papers)
- Select 1 topic when paper has singular, focused research question (~35% of papers)
- Select 2 topics IF:
  • Paper addresses two distinct research areas (method + substance, or two substantive topics), OR
  • Each topic receives ≥30% of the research attention, OR
  • Paper explicitly mentions both areas in title/objectives
  Common 2-topic cases: "soil-spectroscopy|nutrient-cycling" or "litter-decomposition|nitrogen-cycling"
- Select 3 topics ONLY IF paper explicitly compares or integrates three distinct research areas (RARE, <5%)
- If unsure between 1 or 2 topics → default to 2

Redundancy Rule:
Do NOT assign multiple topics if hierarchically related:
❌ soil-carbon + soil-organic-matter (unless paper explicitly distinguishes them)
❌ maom + pom (unless paper directly compares both fractions)
❌ General topic + its routine measurement (e.g., soil-carbon + soil-respiration)

STEP 5: Handle Edge Cases
- REVIEW PAPERS: Broad synthesis spanning 3+ topics without primary focus → "needs-review"
- COMPARATIVE STUDIES: Comparing two substantive topics with equal weight → both topics apply
- METHODOLOGICAL PAPERS: New technique for topic X → "method|topic-x"; Improved protocol → "topic-x" only
- CONTEXTUAL VARIABLES: Papers studying how temperature/moisture affects topic X → topic is X, not the variable

If NO topic fits well: Return "needs-review" as both summary and topic
</topic_selection_process>

%TOPICS%

<examples>
Example 1: Method + Substantive (2 topics)
Title: FTIR spectroscopy reveals functional group changes in mineral-associated organic matter
Output: {"summary": "FTIR Reveals MAOM Functional Groups", "suggested_topic": "soil-spectroscopy|maom"}
Reasoning: Method in title + novel insights about MAOM → both topics

Example 2: Single Substantive Topic (most common)
Title: Cover crops increase soil carbon and reduce erosion
Output: {"summary": "Cover Crops Increase Carbon, Reduce Erosion", "suggested_topic": "cover-crops"}
Reasoning: Primary focus is cover crops. Carbon/erosion are outcomes, not separate foci.

Example 3: Contextual Variable (1 topic)
Title: Temperature sensitivity of MAOM decomposition
Output: {"summary": "Temperature Accelerates MAOM Decomposition", "suggested_topic": "maom"}
Reasoning: Focus is MAOM dynamics. Temperature is experimental variable, not a topic.

Example 4: Legitimate Two Substantive Topics
Title: Comparing MAOM and POM responses to long-term fertilization
Output: {"summary": "Fertilization Differentially Affects MAOM and POM", "suggested_topic": "maom|pom"}
Reasoning: Paper explicitly compares both fractions with equal weight (≥40% each)

Example 5: Needs Review
Title: Soil organic matter dynamics: A comprehensive review
Output: {"summary": "needs-review", "suggested_topic": "needs-review"}
Reasoning: Broad review spanning multiple topics without single primary focus
</examples>

<output_format>
Return ONLY valid JSON with NO additional text:
{
    "summary": "4-6 Word Finding in Title Case",
    "suggested_topic": "topic-one|topic-two"
}

Rules:
- Use exact topic slugs from ALLOWED TOPICS (case-sensitive)
- Separate multiple topics with pipe: maom|pom (NO spaces)
- Summary must be exactly 4-6 words in Title Case
- For needs-review: {"summary": "needs-review", "suggested_topic": "needs-review"}
</output_format>

CRITICAL REMINDERS:
1. You MUST select from ALLOWED TOPICS list - do NOT create new topics
2. Most papers get 2 topics when multiple dimensions exist - default to 2 if uncertain
3. Look for secondary topics: method papers usually pair with substantive topic, process papers often pair with material/system
4. Contextual variables (temperature, moisture, site) are NOT topics
5. When in doubt, use "needs-review" rather than forcing a poor fit"""


def enhance_metadata_with_llm(
    metadata: Dict, api_key: str, model: str = "claude-haiku-4-5-20251001", retry: bool = True, existing_topics: List[str] = None
) -> Dict:
    """
    Enhance metadata with LLM-generated summary and topic suggestion using FIXED TAXONOMY.

    Takes existing metadata (from DOI, PDF metadata, etc.) and uses LLM to:
    - Generate a concise summary (4-6 words) of the key finding
    - Select topic(s) from the fixed taxonomy in topics.yml

    Args:
        metadata: Metadata dict with at minimum 'title', optionally 'abstract' and 'keywords'
        api_key: Anthropic API key
        model: Claude model to use
        retry: Whether to retry once on failure
        existing_topics: Ignored (kept for backwards compatibility)

    Returns:
        Enhanced metadata dict with 'summary' and 'suggested_topic' fields added
    """
    from literature_manager.taxonomy import TopicTaxonomy

    # Load taxonomy
    taxonomy = TopicTaxonomy()

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

    # Get formatted taxonomy for prompt
    topics_section = taxonomy.format_for_prompt()

    prompt = ENHANCEMENT_PROMPT.replace("%TITLE%", title)
    prompt = prompt.replace("%ABSTRACT%", abstract)
    prompt = prompt.replace("%KEYWORDS%", keywords_str)
    prompt = prompt.replace("%TOPICS%", topics_section)

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
            # Validate word count (prefer 4-6 words, accept up to 8)
            word_count = len(summary.split())
            if word_count > 8:
                print(f"Warning: Summary too long ({word_count} words, prefer 4-6): {summary}")
            metadata["summary"] = summary

        if suggested_topic:
            # Validate topics against taxonomy
            topics_list = suggested_topic.split("|")
            valid_topics, invalid_topics = taxonomy.validate_topics(topics_list)

            if invalid_topics:
                print(f"Warning: Invalid topics suggested by LLM: {invalid_topics}")
                print(f"  Paper: {title[:60]}...")
                # Keep only valid topics
                if valid_topics:
                    metadata["suggested_topic"] = "|".join(valid_topics)
                    print(f"  Using valid topics: {valid_topics}")
                else:
                    print(f"  No valid topics - flagging for review")
                    metadata["suggested_topic"] = "needs-review"
            else:
                # All topics valid
                metadata["suggested_topic"] = suggested_topic

                # Check pairing rules if multiple topics
                if len(valid_topics) > 1:
                    for i in range(len(valid_topics)):
                        for j in range(i + 1, len(valid_topics)):
                            allowed, reason = taxonomy.check_pairing_allowed(valid_topics[i], valid_topics[j])
                            if not allowed:
                                print(f"Warning: Disallowed topic pairing: {reason}")
                                print(f"  Paper: {title[:60]}...")

        return metadata

    except Exception as e:
        print(f"LLM enhancement error: {e}")

        # Retry once if requested
        if retry:
            print("Retrying LLM enhancement...")
            return enhance_metadata_with_llm(metadata, api_key, model, retry=False)

        # Fallback: return metadata unchanged
        return metadata


# Enhanced paper summary for Zotero notes
PAPER_SUMMARY_PROMPT = """You are a soil science expert creating a concise research summary.

<paper>
<title>%TITLE%</title>
<abstract>%ABSTRACT%</abstract>
</paper>

Create a structured summary with these sections:

1. MAIN FINDING (2-3 sentences): What did this research discover or conclude? Be specific about the mechanism or relationship identified. Include numbers, conditions, or qualifications if mentioned.

2. KEY APPROACH (1-2 sentences): What methods, framework, experimental design, or data did they use?

3. IMPLICATION (1 sentence): Why does this matter for soil science, land management, or future research?

Write in clear, direct language. Avoid jargon where possible. Be specific rather than vague.

Return ONLY valid JSON:
{
    "main_finding": "2-3 sentences about what was discovered...",
    "key_approach": "1-2 sentences about methods...",
    "implication": "1 sentence about why it matters..."
}"""


def generate_paper_summary(
    metadata: Dict, api_key: str, model: str = "claude-haiku-4-5-20251001"
) -> Dict:
    """
    Generate an enhanced paper summary for Zotero notes.

    Creates a structured summary with main finding, key approach, and implications.

    Args:
        metadata: Metadata dict with 'title' and 'abstract'
        api_key: Anthropic API key
        model: Claude model to use

    Returns:
        Metadata dict with 'enhanced_summary' field added
    """
    title = metadata.get("title", "")
    abstract = metadata.get("abstract", "")

    if not title or not abstract:
        # Can't generate meaningful summary without both
        return metadata

    # Truncate abstract if too long
    if len(abstract) > 4000:
        abstract = abstract[:4000] + "..."

    prompt = PAPER_SUMMARY_PROMPT.replace("%TITLE%", title)
    prompt = prompt.replace("%ABSTRACT%", abstract)

    try:
        client = Anthropic(api_key=api_key)

        message = client.messages.create(
            model=model,
            max_tokens=600,
            temperature=0,
            messages=[{"role": "user", "content": prompt}]
        )

        response_text = message.content[0].text

        # Parse JSON response
        try:
            summary_data = json.loads(response_text)
        except json.JSONDecodeError:
            import re
            json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
            if json_match:
                summary_data = json.loads(json_match.group(0))
            else:
                print(f"Warning: Could not parse summary JSON")
                return metadata

        # Add to metadata
        metadata["enhanced_summary"] = {
            "main_finding": summary_data.get("main_finding", ""),
            "key_approach": summary_data.get("key_approach", ""),
            "implication": summary_data.get("implication", ""),
        }

        return metadata

    except Exception as e:
        print(f"Summary generation error: {e}")
        return metadata


# Full-text paper summary for higher quality summaries
FULLTEXT_SUMMARY_PROMPT = """You are a soil science expert creating a comprehensive research summary from the full paper text.

<paper>
<title>%TITLE%</title>
<full_text>
%FULLTEXT%
</full_text>
</paper>

Read the entire paper carefully and create a detailed summary with these sections:

1. MAIN FINDING (3-4 sentences): What did this research discover or conclude? Be specific about:
   - The key mechanism, relationship, or pattern identified
   - Quantitative results (percentages, rates, effect sizes) where available
   - Important conditions, caveats, or context for the findings

2. KEY APPROACH (2-3 sentences): What methods, framework, or experimental design did they use?
   - Study type (field, lab, modeling, meta-analysis)
   - Key analytical methods or techniques
   - Scale, duration, or sample size if notable

3. KEY DATA/RESULTS (2-3 sentences): What are the most important numbers or findings?
   - Primary quantitative results
   - Statistical significance or confidence
   - Comparisons between treatments or conditions

4. IMPLICATION (1-2 sentences): Why does this matter?
   - Practical implications for soil management or land use
   - Theoretical contributions to soil science
   - Future research directions suggested

Write in clear, direct language. Be specific and quantitative where the paper provides numbers.

Return ONLY valid JSON:
{
    "main_finding": "3-4 sentences about what was discovered...",
    "key_approach": "2-3 sentences about methods...",
    "key_results": "2-3 sentences with important numbers/data...",
    "implication": "1-2 sentences about why it matters..."
}"""


def generate_fulltext_summary(
    pdf_path: str, title: str, api_key: str, model: str = "claude-haiku-4-5-20251001"
) -> Optional[Dict]:
    """
    Generate an enhanced paper summary from full PDF text.

    Creates a detailed summary with main finding, approach, key results, and implications.

    Args:
        pdf_path: Path to the PDF file
        title: Paper title
        api_key: Anthropic API key
        model: Claude model to use

    Returns:
        Dict with enhanced_summary field, or None if failed
    """
    from pathlib import Path
    from literature_manager.extractors.text_parser import extract_text_from_pdf

    pdf_file = Path(pdf_path)
    if not pdf_file.exists():
        return None

    # Extract full text from PDF
    full_text = extract_text_from_pdf(pdf_file)
    if not full_text or len(full_text) < 500:
        return None

    # Truncate if too long (keep ~20k chars which is ~5k tokens)
    if len(full_text) > 25000:
        full_text = full_text[:25000] + "\n\n[Text truncated...]"

    prompt = FULLTEXT_SUMMARY_PROMPT.replace("%TITLE%", title)
    prompt = prompt.replace("%FULLTEXT%", full_text)

    try:
        client = Anthropic(api_key=api_key)

        message = client.messages.create(
            model=model,
            max_tokens=800,
            temperature=0,
            messages=[{"role": "user", "content": prompt}]
        )

        response_text = message.content[0].text

        # Parse JSON response
        try:
            summary_data = json.loads(response_text)
        except json.JSONDecodeError:
            import re
            json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
            if json_match:
                summary_data = json.loads(json_match.group(0))
            else:
                return None

        return {
            "main_finding": summary_data.get("main_finding", ""),
            "key_approach": summary_data.get("key_approach", ""),
            "key_results": summary_data.get("key_results", ""),
            "implication": summary_data.get("implication", ""),
        }

    except Exception as e:
        print(f"Fulltext summary error: {e}")
        return None


# Domain-specific extraction for soil science research
DOMAIN_EXTRACTION_PROMPT = """You are a soil science specialist extracting structured research attributes from paper metadata.

<task>
Extract domain-specific attributes to enable precise filtering and discovery of soil science papers.
</task>

<paper_metadata>
<title>%TITLE%</title>
<abstract>%ABSTRACT%</abstract>
</paper_metadata>

<instructions>
Extract the following structured attributes. Use ONLY values from the allowed lists where specified.
If information is not clearly stated or inferable, use null or empty list [].

1. STUDY_TYPE (pick ONE):
   - "field" (field studies, plot experiments, chronosequences)
   - "laboratory" (incubations, controlled experiments, bench work)
   - "greenhouse" (pot trials, growth chamber)
   - "modeling" (simulation, prediction, machine learning)
   - "review" (meta-analysis, literature review, synthesis)
   - "methods" (method development, validation, comparison)
   - null if unclear

2. ANALYTICAL_METHODS (list all that apply):
   - Spectroscopy: "FTIR", "MIR", "NIR", "VNIR", "NMR", "Raman", "XRF", "XRD"
   - Imaging: "SEM", "TEM", "CT-scan", "synchrotron"
   - Wet chemistry: "wet-oxidation", "dry-combustion", "loss-on-ignition"
   - Biological: "PLFA", "DNA-sequencing", "qPCR", "enzyme-assays", "respiration"
   - Physical: "density-fractionation", "size-fractionation", "aggregate-analysis"
   - Isotopes: "13C", "14C", "15N", "stable-isotopes"
   - Other: "elemental-analyzer", "thermogravimetry"

3. SOIL_FRACTIONS (list all studied):
   - "bulk-soil", "POM", "MAOM", "fPOM", "oPOM", "light-fraction", "heavy-fraction"
   - "sand", "silt", "clay", "aggregates", "microaggregates", "macroaggregates"
   - "dissolved-organic-matter", "microbial-biomass", "root-biomass"

4. DEPTH_INFO:
   - Extract sampling depths if mentioned (e.g., "0-10cm", "0-30cm", "topsoil", "subsoil")
   - Return as list of strings or null if not specified

5. SOIL_PROPERTIES (list properties measured/predicted):
   - Carbon: "SOC", "total-C", "organic-C", "inorganic-C", "black-carbon"
   - Nitrogen: "total-N", "mineral-N", "NO3", "NH4", "N-mineralization"
   - Other nutrients: "P", "K", "Ca", "Mg", "micronutrients"
   - Physical: "bulk-density", "texture", "aggregate-stability", "water-holding"
   - Biological: "microbial-biomass-C", "enzyme-activity", "respiration-rate"

6. ECOSYSTEM_CONTEXT (pick ONE primary):
   - "agricultural", "forest", "grassland", "wetland", "arid", "tropical"
   - "temperate", "boreal", "alpine", "urban", "contaminated"
   - null if not specified or multiple without clear primary

7. MANAGEMENT_PRACTICES (list if applicable):
   - "tillage", "no-till", "cover-crops", "crop-rotation", "organic-amendments"
   - "biochar", "fertilizer", "irrigation", "grazing", "afforestation"
   - "restoration", "remediation"
</instructions>

<output_format>
Return ONLY valid JSON:
{
    "study_type": "field|laboratory|greenhouse|modeling|review|methods",
    "analytical_methods": ["method1", "method2"],
    "soil_fractions": ["fraction1", "fraction2"],
    "depth_info": ["0-10cm", "10-30cm"],
    "soil_properties": ["SOC", "total-N"],
    "ecosystem": "agricultural|forest|grassland|...",
    "management": ["practice1", "practice2"]
}

Use null for unknown single values, [] for unknown lists.
</output_format>"""


def extract_domain_attributes(
    metadata: Dict, api_key: str, model: str = "claude-haiku-4-5-20251001"
) -> Dict:
    """
    Extract domain-specific soil science attributes from paper metadata.

    This supplements the basic metadata with structured attributes useful for
    filtering and discovery: study type, methods, fractions, depths, etc.

    Args:
        metadata: Metadata dict with 'title' and optionally 'abstract'
        api_key: Anthropic API key
        model: Claude model to use

    Returns:
        Metadata dict with 'domain_attributes' field added
    """
    title = metadata.get("title", "")
    abstract = metadata.get("abstract", "")

    if not title:
        return metadata

    if not abstract:
        abstract = "Not available - analyze title only"

    # Truncate abstract if too long
    if len(abstract) > 3000:
        abstract = abstract[:3000] + "..."

    prompt = DOMAIN_EXTRACTION_PROMPT.replace("%TITLE%", title)
    prompt = prompt.replace("%ABSTRACT%", abstract)

    try:
        client = Anthropic(api_key=api_key)

        message = client.messages.create(
            model=model,
            max_tokens=500,
            temperature=0,
            messages=[{"role": "user", "content": prompt}]
        )

        response_text = message.content[0].text

        # Parse JSON response
        try:
            domain_attrs = json.loads(response_text)
        except json.JSONDecodeError:
            import re
            json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
            if json_match:
                domain_attrs = json.loads(json_match.group(0))
            else:
                print(f"Warning: Could not parse domain attributes JSON")
                return metadata

        # Add to metadata
        metadata["domain_attributes"] = {
            "study_type": domain_attrs.get("study_type"),
            "analytical_methods": domain_attrs.get("analytical_methods", []),
            "soil_fractions": domain_attrs.get("soil_fractions", []),
            "depth_info": domain_attrs.get("depth_info", []),
            "soil_properties": domain_attrs.get("soil_properties", []),
            "ecosystem": domain_attrs.get("ecosystem"),
            "management": domain_attrs.get("management", []),
        }

        return metadata

    except Exception as e:
        print(f"Domain extraction error: {e}")
        return metadata
