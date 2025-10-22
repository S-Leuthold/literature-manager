# Literature Manager

**Status:** In Development
**Vision:** Drop PDF → Walk away → It's filed and organized

## Quick Start

```bash
# Set up virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure
cp config.example.yaml config.yaml
# Edit config.yaml with your settings

# Process PDFs
python literature_manager.py process

# Watch inbox continuously
python literature_manager.py watch

# Review low-confidence papers
python literature_manager.py review-recent
```

## Full Documentation

See: `/Users/samleuthold/Desktop/_literature_manager_roadmap.md`

## Structure

```
literature-manager/
├── literature_manager.py     # Main CLI
├── config.yaml               # Configuration
├── requirements.txt          # Dependencies
├── src/
│   ├── metadata_extractor.py
│   ├── file_namer.py
│   ├── topic_matcher.py
│   └── file_operations.py
├── tests/
└── .literature-index.json    # Metadata database (auto-generated)
```

## Configuration

Edit `config.yaml`:
- Set paths (inbox, library, tools)
- Configure confidence thresholds
- Add API keys (CrossRef, Anthropic)
- Adjust topic matching weights

---

**Created:** 2025-10-22
