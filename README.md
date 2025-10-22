# Literature Manager

**Automated PDF literature organization tool with LLM-powered metadata extraction**

Drop a PDF → Walk away → It's filed and organized ✨

## Features

- **Automated metadata extraction** using multiple methods:
  - DOI + CrossRef API lookup (95% confidence)
  - PDF metadata fields (70% confidence)
  - LLM parsing with Claude Haiku 4.5 (80% confidence)
- **Intelligent file naming**: `Author et al., Year - Title.pdf`
- **Topic matching** with TF-IDF similarity and profile learning
- **LLM topic suggestions** for new papers
- **Duplicate detection** (by DOI and title similarity)
- **3-day recent window** for immediate access to new papers
- **Symlink support** for multi-topic papers
- **Interactive review** interface for manual curation
- **Comprehensive logging** and searchable index
- **Watch mode** for automated processing

## Installation

### Prerequisites

- Python 3.10+
- Anthropic API key

### Install

```bash
cd ~/Desktop/workshop/.tools/literature-manager
pip install -e .
```

This installs the `literature-manager` command globally.

### Configuration

1. Copy the example config:
```bash
cp config.yaml.example config.yaml
```

2. Edit `config.yaml`:
   - Set `workshop_root` to your workshop path
   - Add your `anthropic_api_key`
   - Adjust confidence thresholds if needed

3. Create directory structure:
```bash
mkdir -p ~/Desktop/workshop/workspace/inbox
mkdir -p ~/Desktop/workshop/library/literature/{recent,unknowables,by-topic}
```

## Usage

### Basic Workflow

1. **Drop PDFs in inbox:**
```bash
cp paper.pdf ~/Desktop/workshop/workspace/inbox/
```

2. **Process automatically:**
```bash
literature-manager process
```

Or use watch mode:
```bash
literature-manager watch
```

### CLI Commands

#### `process`
Process all PDFs in inbox:
```bash
literature-manager process              # Process all PDFs
literature-manager process --dry-run    # Preview without changes
literature-manager process --quiet      # Minimal output
```

#### `watch`
Monitor inbox and process new PDFs automatically:
```bash
literature-manager watch
```
Press Ctrl+C to stop.

#### `stats`
Show library statistics:
```bash
literature-manager stats
```

Displays:
- Total papers
- Papers by topic
- Papers by year
- Papers by extraction method
- Papers needing review

#### `review-recent`
Interactively review papers in `recent/`:
```bash
literature-manager review-recent
```

Options for each paper:
- `[a]` Accept LLM-suggested topic
- `[c]` Choose from existing topics
- `[n]` Create new topic
- `[s]` Skip for now
- `[q]` Quit review

#### `cleanup`
Remove old papers from `recent/` (older than 3 days):
```bash
literature-manager cleanup
```

Moves old papers to `unknowables/` for manual review.

## How It Works

### Metadata Extraction

The system tries methods in priority order:

1. **DOI + CrossRef** (preferred):
   - Searches PDF for DOI
   - Looks up metadata from CrossRef API
   - 95% confidence, most reliable

2. **PDF Metadata**:
   - Extracts title, author, year from PDF properties
   - 70% confidence

3. **LLM Parsing** (fallback):
   - Extracts text from first 3 pages
   - Uses Claude Haiku 4.5 to parse metadata
   - Suggests topic name
   - 80% confidence

### Topic Matching

- Extracts keywords from title + abstract
- Compares to existing topic profiles using TF-IDF cosine similarity
- Considers author overlap and year proximity
- **High confidence (≥85%) + established topic (≥3 papers)** → auto-file to `by-topic/[topic]/`
- **Low confidence or new topic** → file to `recent/` for review

### Topic Learning

- Each topic builds a profile: keywords, common authors, year range
- Profiles update as you add papers
- After 3 papers, topic becomes "established" and eligible for auto-filing

### File Organization

```
workshop/
├── workspace/
│   └── inbox/                    # Drop PDFs here
├── library/
│   └── literature/
│       ├── recent/               # Last 3 days (all papers)
│       ├── unknowables/          # Failed extractions
│       └── by-topic/
│           ├── soil-carbon/
│           ├── fractionation-methods/
│           └── spectroscopy/
└── .tools/
    └── literature-manager/
        ├── .literature-index.json     # Searchable metadata
        ├── .literature-log.txt        # Processing history
        └── .topic-profiles.json       # Learned topic characteristics
```

### Recent Window

All papers are copied to `recent/` for 3 days, regardless of where they're filed. This gives you immediate access to new papers without navigating the topic structure.

After 3 days, `literature-manager cleanup` moves them to `unknowables/` if not filed to a topic.

## Configuration Options

Key settings in `config.yaml`:

```yaml
# Processing
confidence_threshold: 0.85         # Min confidence for auto-filing
min_papers_for_topic: 3            # Min papers before topic is "established"
recent_retention_days: 3           # Days to keep papers in recent/
always_copy_to_recent: true        # Copy all papers to recent/

# LLM
llm_model: "claude-haiku-4-20250514"  # Fast and cheap
anthropic_api_key: "sk-ant-..."

# Naming
max_title_words: 8                 # Max words in shortened title
max_filename_length: 200           # Max total filename length

# Duplicates
duplicate_action: "merge"          # Options: merge, skip, prompt
duplicate_keep_larger: true        # Keep larger file when merging
```

## Cost Estimate

Using Claude Haiku 4.5 (~$0.80/million input tokens, ~$4/million output tokens):

- **Per paper:** ~$0.001-0.002 (0.1-0.2 cents)
- **100 papers/month:** ~$0.10-0.20
- **500 papers total:** ~$1-2

DOI lookup is free and preferred, so LLM is only used as fallback.

## Troubleshooting

### PDFs not processing

1. Check inbox path:
```bash
ls ~/Desktop/workshop/workspace/inbox/
```

2. Run with verbose output:
```bash
literature-manager process --verbose
```

3. Check logs:
```bash
cat ~/Desktop/workshop/.tools/literature-manager/.literature-log.txt
```

### Papers going to unknowables

- Check if PDF is text-based (not scanned image)
- Verify API key is set correctly
- Try manual extraction: open PDF and check if title/DOI is visible

### Topic matching not working

- Need at least 3 papers per topic before auto-filing works
- Use `literature-manager review-recent` to manually assign first papers
- Check topic profiles:
```bash
cat ~/Desktop/workshop/.tools/literature-manager/.topic-profiles.json
```

### Duplicate detection too aggressive

Adjust fuzzy match threshold in code (operations.py, line ~200):
```python
similarity >= 0.90  # Lower this value (e.g., 0.85)
```

## Development

### Project Structure

```
src/literature_manager/
├── __init__.py
├── __main__.py
├── cli.py                # Command-line interface
├── config.py             # Configuration loading
├── naming.py             # File naming logic
├── operations.py         # File ops, indexing, logging
├── topics.py             # Topic matching and learning
├── utils.py              # Utility functions
└── extractors/
    ├── __init__.py
    ├── orchestrator.py   # Coordinates extraction methods
    ├── doi.py            # DOI + CrossRef lookup
    ├── pdf_metadata.py   # PDF metadata extraction
    ├── text_parser.py    # Text extraction
    └── llm.py            # LLM-based extraction
```

### Running Tests

```bash
pytest tests/ -v
pytest tests/ --cov=literature_manager --cov-report=html
```

### Code Formatting

```bash
black src/literature_manager/
ruff src/literature_manager/
```

## Roadmap

### Phase 1: MVP (Complete!)
- ✅ PDF metadata extraction
- ✅ Automatic file naming
- ✅ Topic matching
- ✅ File operations
- ✅ CLI interface

### Phase 2: Enhancements
- [ ] Search command (full-text search across abstracts)
- [ ] Export to BibTeX
- [ ] Related papers finder
- [ ] Topic consolidation suggestions
- [ ] Performance optimization for large libraries

### Phase 3: Integrations
- [ ] Zotero sync
- [ ] Calendar integration for reading queue
- [ ] Web interface
- [ ] Mobile notifications

## License

MIT

## Author

Sam Leuthold

---

**Version:** 0.1.0
**Last Updated:** 2025-10-22
