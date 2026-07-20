"""Topic taxonomy management for literature categorization."""

import logging

import yaml
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_REQUIRED_TOPIC_KEYS = {"slug", "category", "description"}


def _find_topics_yml() -> Path:
    """Locate topics.yml robustly.

    topics.yml now ships inside the package (declared as package-data), so the
    packaged copy next to this module is the canonical location. The other
    candidates are fallbacks for running from a source checkout or overriding
    via env. Search, in order: an explicit LITERATURE_MANAGER_TOPICS env var,
    the packaged copy, the current working directory (the daemon's
    WorkingDirectory), and the legacy repo-root layout. First existing wins;
    else raise with the list tried.
    """
    import os

    candidates = []
    env = os.getenv("LITERATURE_MANAGER_TOPICS")
    if env:
        candidates.append(Path(env))
    candidates.append(Path(__file__).parent / "topics.yml")                # packaged (canonical)
    candidates.append(Path.cwd() / "topics.yml")                           # WorkingDirectory
    candidates.append(Path(__file__).parent.parent.parent / "topics.yml")  # legacy repo layout
    for c in candidates:
        if c.is_file():
            return c
    raise FileNotFoundError(
        "topics.yml not found. Looked in: " + ", ".join(str(c) for c in candidates)
    )


class TopicTaxonomy:
    """Manages the fixed topic taxonomy for paper categorization."""

    def __init__(self, taxonomy_path: Optional[Path] = None):
        """
        Load topic taxonomy from YAML file.

        Args:
            taxonomy_path: Path to topics.yml file. If None, uses default location.
        """
        if taxonomy_path is None:
            taxonomy_path = _find_topics_yml()

        with open(taxonomy_path, "r") as f:
            self.data = yaml.safe_load(f)

        # Validate and drop malformed topic entries so a single bad entry can't
        # crash processing later (the May-2026 KeyError: 'description' class).
        # Skip-and-warn keeps the watcher alive even if topics.yml regresses.
        valid_topics = []
        for i, topic in enumerate(self.data.get("topics", [])):
            missing = _REQUIRED_TOPIC_KEYS - set(topic.keys())
            if missing:
                logging.warning(
                    "topics.yml entry %d (slug=%s) missing %s — skipping",
                    i, topic.get("slug", "?"), sorted(missing),
                )
                continue
            valid_topics.append(topic)
        self.data["topics"] = valid_topics

        # Build lookup structures
        self._topics_by_slug = {topic["slug"]: topic for topic in self.data["topics"]}
        self._topics_by_category = {}
        for topic in self.data["topics"]:
            category = topic["category"]
            if category not in self._topics_by_category:
                self._topics_by_category[category] = []
            self._topics_by_category[category].append(topic)

    def get_all_topics(self) -> List[Dict]:
        """Get all topics as list of dicts."""
        return self.data["topics"]

    def get_topic(self, slug: str) -> Optional[Dict]:
        """Get topic by slug."""
        return self._topics_by_slug.get(slug)

    def get_topics_by_category(self, category: str) -> List[Dict]:
        """Get all topics in a category."""
        return self._topics_by_category.get(category, [])

    def get_all_slugs(self) -> List[str]:
        """Get list of all topic slugs."""
        return [topic["slug"] for topic in self.data["topics"]]

    def get_categories(self) -> List[str]:
        """Get list of all categories."""
        return self.data["categories"]

    def format_for_prompt(self) -> str:
        """
        Format taxonomy for inclusion in LLM prompt.

        Returns formatted string organized by category with topic names and descriptions.
        """
        lines = []
        lines.append("ALLOWED TOPICS:")
        lines.append("")

        for category in self.data["categories"]:
            # Format category name
            category_name = category.replace("-", " ").title()
            topics = self.get_topics_by_category(category)

            lines.append(f"## {category_name} ({len(topics)} topics)")
            lines.append("")

            for topic in topics:
                lines.append(f"- **{topic.get('slug', '?')}**: {topic.get('description', '')}")

            lines.append("")

        return "\n".join(lines)

    def validate_topics(self, topics: List[str]) -> Tuple[List[str], List[str]]:
        """
        Validate a list of topic slugs.

        Args:
            topics: List of topic slugs to validate

        Returns:
            Tuple of (valid_topics, invalid_topics)
        """
        valid = []
        invalid = []

        for topic in topics:
            if topic in self._topics_by_slug:
                valid.append(topic)
            else:
                invalid.append(topic)

        return valid, invalid

    def check_pairing_allowed(self, topic1: str, topic2: str) -> Tuple[bool, Optional[str]]:
        """
        Check if two topics can be paired together.

        Args:
            topic1: First topic slug
            topic2: Second topic slug

        Returns:
            Tuple of (allowed, reason)
        """
        # Check disallowed pairs
        disallowed = self.data["pairing_rules"]["disallowed"]
        for pair in disallowed:
            if set([topic1, topic2]) == set(pair):
                return False, f"Disallowed pair: {pair[0]} and {pair[1]} are too redundant"

        # Check if both topics exist
        if topic1 not in self._topics_by_slug or topic2 not in self._topics_by_slug:
            return False, "One or both topics do not exist"

        # Pairing allowed
        return True, None

    def get_max_topics(self) -> int:
        """Get maximum number of topics allowed per paper."""
        return self.data["pairing_rules"]["max_topics"]

    def is_method_topic(self, topic_slug: str) -> bool:
        """Check if topic is an analytical method."""
        topic = self.get_topic(topic_slug)
        if topic:
            return topic["category"] == "analytical-methods"
        return False
