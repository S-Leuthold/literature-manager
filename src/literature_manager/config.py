"""Configuration management for Literature Manager."""

import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from dotenv import load_dotenv


class Config:
    """Configuration loader and manager."""

    def __init__(self, config_path: Optional[Path] = None):
        """
        Initialize configuration.

        Args:
            config_path: Path to config.yaml file. If None, searches default locations.
        """
        self.config_path = config_path or self._find_config()
        self.data = self._load_config()
        self._resolve_paths()
        self._load_env()

    def _find_config(self) -> Path:
        """Find config.yaml in default locations."""
        # Try current directory
        if Path("config.yaml").exists():
            return Path("config.yaml")

        # Try package directory
        package_dir = Path(__file__).parent.parent.parent
        config_path = package_dir / "config.yaml"
        if config_path.exists():
            return config_path

        # Try workshop/.tools/literature-manager
        workshop_tools = Path.home() / "Desktop/workshop/.tools/literature-manager/config.yaml"
        if workshop_tools.exists():
            return workshop_tools

        raise FileNotFoundError(
            "config.yaml not found. Please create one from config.yaml.example"
        )

    def _load_config(self) -> Dict[str, Any]:
        """Load YAML config file."""
        try:
            with open(self.config_path, "r") as f:
                data = yaml.safe_load(f)
            return data or {}
        except Exception as e:
            raise ValueError(f"Failed to load config from {self.config_path}: {e}")

    def _resolve_paths(self):
        """Resolve all paths to absolute paths."""
        workshop_root = Path(self.data.get("workshop_root", "~/Desktop/workshop")).expanduser()

        # Main directories
        self.workshop_root = workshop_root
        self.inbox_path = workshop_root / self.data.get("inbox_path", "workspace/inbox")
        self.library_path = workshop_root / self.data.get("library_path", "library/literature")
        self.tools_path = workshop_root / self.data.get("tools_path", ".tools/literature-manager")

        # Literature subdirectories
        self.recent_path = self.library_path / "recent"
        self.unknowables_path = self.library_path / "unknowables"
        self.corrupted_path = self.library_path / "corrupted"
        self.by_topic_path = self.library_path / "by-topic"

        # Data files
        self.index_path = self.tools_path / ".literature-index.json"
        self.log_path = self.tools_path / ".literature-log.txt"

    def _load_env(self):
        """Load API keys from .env file in .tools/ directory."""
        env_path = self.workshop_root / ".tools" / ".env"
        if env_path.exists():
            load_dotenv(env_path)

        # Load API keys from environment into config data
        if os.getenv("ANTHROPIC_API_KEY"):
            self.data["anthropic_api_key"] = os.getenv("ANTHROPIC_API_KEY")

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value."""
        return self.data.get(key, default)

    def ensure_directories(self):
        """Create all necessary directories if they don't exist."""
        dirs_to_create = [
            self.inbox_path,
            self.recent_path,
            self.unknowables_path,
            self.corrupted_path,
            self.by_topic_path,
            self.tools_path,
        ]

        for directory in dirs_to_create:
            directory.mkdir(parents=True, exist_ok=True)

    def __repr__(self) -> str:
        return f"Config(config_path={self.config_path})"


def load_config(config_path: Optional[Path] = None) -> Config:
    """
    Load configuration.

    Args:
        config_path: Optional path to config.yaml

    Returns:
        Config object
    """
    return Config(config_path)
