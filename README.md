# Literature Manager

> Automated academic PDF organization with LLM-powered categorization and Zotero integration

A command-line tool for researchers that automatically extracts metadata, assigns topics, generates finding-focused summaries, and organizes academic papers into a searchable library.

## Features

**Drop a PDF, get an organized library.** Literature Manager handles the tedious work of managing research papers:

- **Extracts metadata** from DOIs, PDF properties, or LLM parsing (97% success rate)
- **Assigns topics** using a customizable research taxonomy (51 topics, 7 categories)
- **Generates summaries** - both short (6-8 words for filenames) and detailed (4-paragraph analysis)
- **Organizes files** with intelligent naming: `Author et al., Year - Finding Summary.pdf`
- **Syncs to Zotero** with collections, tags, and attached notes
- **Runs hands-free** as a background service (macOS)

**Designed for soil scientists and biogeochemists**, but easily adaptable to any research domain.

## Quick Start

```bash
# Clone and install
git clone https://github.com/yourusername/literature-manager.git
cd literature-manager
pip install -e .

# Configure
cp config.yaml.example config.yaml
# Edit config.yaml with your paths

# Add API key
echo "ANTHROPIC_API_KEY=your_key" >> ../.env

# Process papers
cp ~/Downloads/*.pdf ~/Desktop/workshop/workspace/inbox/
literature-manager process
```

## What It Does

**Input:** `10.1038_s41586-021-03306-8.pdf`

**Processing:**
1. Extracts DOI from filename/PDF text
2. Looks up metadata from CrossRef (title, authors, year, abstract)
3. LLM generates summary: *"Mineral Protection Preserves Long-Term Soil Carbon"*
4. Assigns topics: `maom`, `stabilization-mechanisms`
5. Renames file: `Hemingway et al., 2019 - Mineral Protection Preserves Long-Term Soil Carbon.pdf`
6. Files to `by-topic/maom/` with symlink in `stabilization-mechanisms/`
7. Uploads to Zotero with collections and tags

**Time:** 3-5 seconds | **Cost:** ~$0.001 per paper

## Commands

### Core Workflow

| Command | Description |
|---------|-------------|
| `process` | Process all PDFs in inbox |
| `watch` | Auto-process new PDFs continuously |
| `stats` | Show library statistics |
| `search` | Search papers by attributes |

### Enrichment

| Command | Description |
|---------|-------------|
| `summarize-fulltext` | Generate 4-paragraph summaries from full PDF text |
| `enrich-summaries` | Generate enhanced summaries for existing papers |
| `enrich` | Add domain-specific attributes |

### Maintenance

| Command | Description |
|---------|-------------|
| `repair-metadata` | Fix papers with garbled titles, PII, or wrong years |
| `repair-from-filename` | Recover author/year from original filename |
| `reprocess` | Re-extract metadata for papers with poor quality |
| `backfill-dois` | Find DOIs for papers missing them |
| `backfill-citations` | Add journal, volume, pages from CrossRef |
| `dedup` | Find and remove duplicate papers |
| `cleanup` | Remove papers older than 3 days from recent/ |
| `review-recent` | Interactive review of recent papers |

### Zotero Integration

| Command | Description |
|---------|-------------|
| `sync-zotero` | Sync enhanced summaries as notes |
| `zotero-update-summaries` | Push fulltext summaries to Zotero |
| `zotero-update-citations` | Update Zotero items with citation metadata |
| `zotero-dedup` | Find and remove duplicate Zotero items |

### Example Usage

```bash
# Basic processing
literature-manager process              # Process all inbox PDFs
literature-manager process --dry-run    # Preview without changes

# Background service
literature-manager watch                # Watch mode (Ctrl+C to stop)
./install_background_service.sh         # Install as macOS service

# Enrich existing library
literature-manager summarize-fulltext --limit 100   # Generate detailed summaries
literature-manager backfill-citations --limit 500   # Add citation metadata

# Fix problems
literature-manager repair-metadata --dry-run        # Preview metadata repairs
literature-manager dedup                            # Remove duplicates

# Zotero sync
literature-manager zotero-update-summaries --limit 1000
```

## Performance

**Tested on 940+ papers:**

| Metric | Value |
|--------|-------|
| Metadata extraction success | 97% |
| DOI coverage | 94% |
| Fulltext summary coverage | 95% |
| Processing speed | ~4 sec/paper |
| Total library cost | ~$1.50 |

## Configuration

### Directory Structure

```
workshop/
├── workspace/inbox/              # Drop PDFs here
└── library/literature/
    ├── by-topic/                 # Organized papers
    │   ├── soil-carbon/
    │   ├── maom/
    │   └── ...
    ├── recent/                   # 3-day holding area
    ├── unknowables/              # Papers needing review
    └── corrupted/                # Unreadable PDFs
```

### Topic Taxonomy

51 topics across 7 categories in `topics.yml`:

- **Soil Carbon & Organic Matter** (8): soil-carbon, maom, pom, fractionation-methods...
- **Analytical Methods** (8): spectroscopy, isotope-methods, molecular-methods...
- **Biogeochemical Processes** (8): decomposition, priming, stabilization-mechanisms...
- **Agricultural Systems** (10): cover-crops, tillage, amendments...
- **Environmental & Climate** (7): climate-change, carbon-cycling, ecosystem-services...
- **Soil Properties & Processes** (5): soil-structure, microbial-ecology...
- **Social Science & Policy** (5): carbon-markets, modeling...

Customize by editing `topics.yml` - no code changes needed.

### Zotero Integration

1. Get API credentials at https://www.zotero.org/settings/keys
2. Add to `../.env`:
   ```
   ZOTERO_API_KEY=your_key
   ZOTERO_USER_ID=your_id
   ```
3. Enable in `config.yaml`:
   ```yaml
   zotero_sync_enabled: true
   ```

**Features:**
- Auto-upload after processing
- Collections created for each topic
- Multi-topic papers appear in multiple collections
- Tags from assigned topics
- Fulltext summaries as attached notes

## Installation

### Prerequisites

- Python 3.9+
- [Anthropic API key](https://console.anthropic.com/settings/keys)
- (Optional) [Zotero account](https://www.zotero.org/) for sync features

### Detailed Setup

```bash
# Clone repository
git clone https://github.com/yourusername/literature-manager.git
cd literature-manager

# Create virtual environment (recommended)
python -m venv venv
source venv/bin/activate

# Install package
pip install -e .

# Set up configuration
cp config.yaml.example config.yaml
# Edit paths in config.yaml

# Set up environment variables
cp ../.env.example ../.env
# Add API keys to ../.env

# Create directories
mkdir -p ~/Desktop/workshop/workspace/inbox
mkdir -p ~/Desktop/workshop/library/literature/by-topic

# Test installation
literature-manager --help
literature-manager stats
```

### Background Service (macOS)

For fully automated processing:

```bash
./install_background_service.sh
```

This installs a launchd service that:
- Starts automatically on login
- Monitors inbox continuously
- Processes papers immediately
- Runs with low priority

**View logs:**
```bash
tail -f logs/watch.log        # Processing activity
tail -f logs/watch.error.log  # Errors only
```

**Manage service:**
```bash
# Check status
launchctl list | grep literature

# Restart
launchctl kickstart -k gui/$(id -u)/com.samleuthold.literature-manager

# Stop
launchctl unload ~/Library/LaunchAgents/com.samleuthold.literature-manager.plist
```

## Architecture

### Processing Pipeline

```
PDF → DOI Extraction → CrossRef Lookup → Abstract Recovery →
LLM Enhancement → Topic Assignment → Duplicate Check →
File Organization → Index Update → Zotero Upload
```

### Key Components

```
literature-manager/
├── src/literature_manager/
│   ├── cli.py                # 20 CLI commands
│   ├── config.py             # Configuration management
│   ├── taxonomy.py           # Fixed topic system
│   ├── zotero_sync.py        # Zotero API integration
│   ├── index_validator.py    # Path validation & repair
│   ├── operations.py         # File operations
│   ├── naming.py             # Filename generation
│   └── extractors/
│       ├── orchestrator.py   # Coordinates extraction
│       ├── doi.py            # DOI + CrossRef lookup
│       ├── pdf_metadata.py   # PDF metadata extraction
│       ├── text_parser.py    # Text extraction
│       └── llm.py            # LLM parsing & enhancement
├── topics.yml                # Research taxonomy
├── config.yaml.example       # Configuration template
└── install_background_service.sh
```

## Troubleshooting

### Papers fail to process
- Check if PDF is text-based (not scanned image)
- Verify DOI is present and formatted correctly
- Check API key is set in `../.env`

### "Another instance is already running"
```bash
rm logs/watch.pid  # Delete stale PID file
```

### Duplicate detection too aggressive
- Adjust threshold in `operations.py` (default 0.90)

### Zotero upload fails
- Check storage quota (free accounts: 300 MB)
- Verify API key has file upload permissions

## Development

### Running Tests

```bash
pytest tests/ -v
pytest tests/ --cov=literature_manager
```

### Contributing

Contributions welcome! Areas for improvement:

- Additional taxonomy domains (medicine, engineering, etc.)
- Alternative LLM providers (OpenAI, local models)
- Linked file support for Zotero
- Windows/Linux background service support
- ISBN lookup for books

## License

MIT License - see [LICENSE](LICENSE) file

## Acknowledgments

Built with:
- [pyzotero](https://github.com/urschrei/pyzotero) - Zotero API client
- [Anthropic Claude](https://www.anthropic.com/) - LLM processing
- [pdfplumber](https://github.com/jsvine/pdfplumber) - PDF text extraction
- [watchdog](https://github.com/gorakhargosh/watchdog) - File system monitoring

---

**Version:** 2.1.0
**Status:** Production
**Last Updated:** 2025-12-05
