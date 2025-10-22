"""Topic matching and profile learning."""

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


@dataclass
class TopicProfile:
    """Profile for a literature topic."""

    name: str
    paper_count: int
    keywords: Dict[str, float]  # keyword -> weight
    common_authors: List[str]
    year_range: Tuple[int, int]

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "name": self.name,
            "paper_count": self.paper_count,
            "keywords": self.keywords,
            "common_authors": self.common_authors,
            "year_range": list(self.year_range),
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "TopicProfile":
        """Create from dictionary."""
        return cls(
            name=data["name"],
            paper_count=data["paper_count"],
            keywords=data["keywords"],
            common_authors=data["common_authors"],
            year_range=tuple(data["year_range"]),
        )


def load_topic_profiles(path: Path) -> Dict[str, TopicProfile]:
    """
    Load topic profiles from JSON file.

    Args:
        path: Path to topic profiles JSON file

    Returns:
        Dictionary of topic name -> TopicProfile
    """
    if not path.exists():
        return {}

    try:
        with open(path, "r") as f:
            data = json.load(f)

        profiles = {}
        for name, profile_data in data.items():
            profiles[name] = TopicProfile.from_dict(profile_data)

        return profiles

    except Exception:
        return {}


def save_topic_profiles(profiles: Dict[str, TopicProfile], path: Path):
    """
    Save topic profiles to JSON file.

    Args:
        profiles: Dictionary of topic profiles
        path: Path to save to
    """
    data = {name: profile.to_dict() for name, profile in profiles.items()}

    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def extract_keywords(text: str, max_keywords: int = 20) -> List[str]:
    """
    Extract significant keywords from text.

    Args:
        text: Text to extract keywords from (title + abstract + keywords)
        max_keywords: Maximum number of keywords to return

    Returns:
        List of keywords
    """
    if not text:
        return []

    # Convert to lowercase
    text = text.lower()

    # Common stop words to filter
    stop_words = {
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "as",
        "is",
        "was",
        "are",
        "were",
        "been",
        "be",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "can",
        "this",
        "that",
        "these",
        "those",
        "we",
        "our",
        "using",
        "used",
    }

    # Extract words (alphanumeric sequences, allow hyphens)
    words = re.findall(r"\b[\w-]+\b", text)

    # Filter stop words and short words
    keywords = [w for w in words if w not in stop_words and len(w) > 3]

    # Count frequency
    freq = {}
    for kw in keywords:
        freq[kw] = freq.get(kw, 0) + 1

    # Sort by frequency and return top N
    sorted_keywords = sorted(freq.items(), key=lambda x: x[1], reverse=True)
    return [kw for kw, _ in sorted_keywords[:max_keywords]]


def match_topic(
    metadata: Dict, profiles: Dict[str, TopicProfile], config: Dict
) -> Tuple[Optional[str], float]:
    """
    Match paper to best topic based on keyword similarity.

    Args:
        metadata: Paper metadata
        profiles: Available topic profiles
        config: Configuration dict with weights

    Returns:
        Tuple of (topic_name, confidence_score) or (None, 0.0)
    """
    if not profiles:
        return None, 0.0

    # Extract text for comparison
    text_parts = []
    if metadata.get("title"):
        text_parts.append(metadata["title"])
    if metadata.get("abstract"):
        text_parts.append(metadata["abstract"])
    if metadata.get("keywords"):
        text_parts.append(" ".join(metadata["keywords"]))

    paper_text = " ".join(text_parts).lower()

    if not paper_text:
        return None, 0.0

    # Get weights from config
    keyword_weight = config.get("keyword_weight", 0.6)
    author_weight = config.get("author_weight", 0.2)
    year_weight = config.get("year_weight", 0.2)

    best_topic = None
    best_score = 0.0

    for topic_name, profile in profiles.items():
        # Keyword similarity using TF-IDF
        topic_text = " ".join(profile.keywords.keys())

        try:
            vectorizer = TfidfVectorizer()
            vectors = vectorizer.fit_transform([paper_text, topic_text])
            similarity = cosine_similarity(vectors[0:1], vectors[1:2])[0][0]
        except Exception:
            similarity = 0.0

        keyword_score = similarity

        # Author overlap score
        author_score = 0.0
        paper_authors = metadata.get("authors", [])
        if paper_authors and profile.common_authors:
            # Check if any author appears in profile
            for author in paper_authors:
                if author in profile.common_authors:
                    author_score = 1.0
                    break

        # Year proximity score
        year_score = 0.0
        paper_year = metadata.get("year")
        if paper_year and profile.year_range:
            min_year, max_year = profile.year_range
            if min_year <= paper_year <= max_year:
                year_score = 1.0
            else:
                # Decay based on distance from range
                distance = min(abs(paper_year - min_year), abs(paper_year - max_year))
                year_score = max(0.0, 1.0 - (distance / 10.0))

        # Weighted combination
        total_score = (
            keyword_score * keyword_weight
            + author_score * author_weight
            + year_score * year_weight
        )

        if total_score > best_score:
            best_score = total_score
            best_topic = topic_name

    return best_topic, best_score


def update_topic_profile(
    topic_name: str, metadata: Dict, profiles: Dict[str, TopicProfile]
) -> Dict[str, TopicProfile]:
    """
    Update topic profile with new paper.

    Args:
        topic_name: Topic to update
        metadata: Paper metadata
        profiles: Current profiles dict

    Returns:
        Updated profiles dict
    """
    if topic_name not in profiles:
        # Create new profile
        profiles[topic_name] = TopicProfile(
            name=topic_name,
            paper_count=0,
            keywords={},
            common_authors=[],
            year_range=(metadata.get("year", 2024), metadata.get("year", 2024)),
        )

    profile = profiles[topic_name]

    # Increment paper count
    profile.paper_count += 1

    # Extract and merge keywords
    text_parts = []
    if metadata.get("title"):
        text_parts.append(metadata["title"])
    if metadata.get("abstract"):
        text_parts.append(metadata["abstract"])
    if metadata.get("keywords"):
        text_parts.append(" ".join(metadata["keywords"]))

    text = " ".join(text_parts)
    new_keywords = extract_keywords(text)

    # Update keyword weights (weighted average with existing)
    for kw in new_keywords:
        if kw in profile.keywords:
            # Increase weight
            profile.keywords[kw] = min(1.0, profile.keywords[kw] + 0.1)
        else:
            # Add new keyword with initial weight
            profile.keywords[kw] = 0.3

    # Keep top 50 keywords
    if len(profile.keywords) > 50:
        sorted_kw = sorted(profile.keywords.items(), key=lambda x: x[1], reverse=True)
        profile.keywords = dict(sorted_kw[:50])

    # Add author if not present (keep top 20)
    paper_authors = metadata.get("authors", [])
    for author in paper_authors[:3]:  # Only consider first 3 authors
        if author not in profile.common_authors:
            profile.common_authors.append(author)

    if len(profile.common_authors) > 20:
        profile.common_authors = profile.common_authors[:20]

    # Expand year range
    paper_year = metadata.get("year")
    if paper_year:
        min_year, max_year = profile.year_range
        profile.year_range = (min(min_year, paper_year), max(max_year, paper_year))

    return profiles


def create_topic_from_papers(
    topic_name: str, papers_metadata: List[Dict]
) -> TopicProfile:
    """
    Create initial topic profile from a list of papers.

    Args:
        topic_name: Name for the topic
        papers_metadata: List of paper metadata dicts

    Returns:
        TopicProfile
    """
    profile = TopicProfile(
        name=topic_name, paper_count=0, keywords={}, common_authors=[], year_range=(9999, 0)
    )

    profiles = {topic_name: profile}

    for metadata in papers_metadata:
        profiles = update_topic_profile(topic_name, metadata, profiles)

    return profiles[topic_name]
