"""Zotero library synchronization."""

import os
from pathlib import Path
from typing import Dict, List, Optional
from pyzotero import zotero
from dotenv import load_dotenv


class ZoteroSync:
    """Handles automatic synchronization with Zotero library."""

    def __init__(self):
        """Initialize Zotero client from environment variables."""
        # Load .env file from .tools/ directory (shared across tools)
        env_path = Path(__file__).parent.parent.parent.parent / '.env'
        load_dotenv(env_path)

        self.api_key = os.getenv('ZOTERO_API_KEY')
        self.user_id = os.getenv('ZOTERO_USER_ID')
        self.library_type = os.getenv('ZOTERO_LIBRARY_TYPE', 'user')

        if not self.api_key or not self.user_id:
            raise ValueError(
                "Zotero credentials not found. "
                "Set ZOTERO_API_KEY and ZOTERO_USER_ID in .env file"
            )

        # Initialize Zotero client
        self.zot = zotero.Zotero(self.user_id, self.library_type, self.api_key)

        # Cache collections (populated on first use)
        self._collections_cache = None

    def get_or_create_collection(self, topic_name: str) -> str:
        """
        Get or create a Zotero collection for a topic.

        Args:
            topic_name: Topic name (e.g., 'soil-carbon', 'maom')

        Returns:
            Collection key
        """
        # Load collections cache
        if self._collections_cache is None:
            self._collections_cache = {}
            collections = self.zot.collections()
            for coll in collections:
                self._collections_cache[coll['data']['name']] = coll['key']

        # Check if collection exists
        if topic_name in self._collections_cache:
            return self._collections_cache[topic_name]

        # Create new collection
        new_coll = self.zot.create_collections([{
            'name': topic_name,
            'parentCollection': False
        }])

        coll_key = new_coll['successful']['0']['key']
        self._collections_cache[topic_name] = coll_key

        return coll_key

    def check_exists(self, doi: Optional[str] = None, title: Optional[str] = None) -> Optional[str]:
        """
        Check if paper already exists in Zotero.

        Args:
            doi: Paper DOI
            title: Paper title (fallback if no DOI)

        Returns:
            Item key if exists, None otherwise
        """
        # Try DOI first (most reliable)
        if doi:
            try:
                results = self.zot.items(q=doi, qmode='everything')
                if results:
                    return results[0]['key']
            except Exception:
                pass

        # Fallback to title search
        if title:
            try:
                # Search for exact title
                results = self.zot.items(q=f'"{title}"', qmode='titleCreatorYear')
                if results:
                    # Check if title actually matches (not just substring)
                    for item in results:
                        item_title = item['data'].get('title', '')
                        if item_title.lower().strip() == title.lower().strip():
                            return item['key']
            except Exception:
                pass

        return None

    def upload_paper(
        self,
        metadata: Dict,
        pdf_path: Path,
        topics: List[str],
        update_if_exists: bool = True
    ) -> Optional[str]:
        """
        Upload paper to Zotero with metadata, PDF, tags, and collections.

        Args:
            metadata: Paper metadata dict
            pdf_path: Path to PDF file
            topics: List of topic slugs
            update_if_exists: If True, update existing item with tags/collections

        Returns:
            Zotero item key if successful, None otherwise
        """
        try:
            doi = metadata.get('doi')
            title = metadata.get('title')

            # Check if already exists
            existing_key = self.check_exists(doi, title)

            if existing_key:
                if update_if_exists:
                    # Update tags and collections
                    self._update_item_tags_collections(existing_key, topics)
                    print(f"  ℹ Updated existing Zotero item: {existing_key}")
                else:
                    print(f"  ℹ Paper already in Zotero, skipping")
                return existing_key

            # Create new item
            template = self.zot.item_template('journalArticle')

            # Basic metadata
            template['title'] = title or ''
            template['DOI'] = doi or ''
            template['date'] = str(metadata.get('year', ''))
            template['abstractNote'] = metadata.get('abstract') or ''

            # Authors
            authors = metadata.get('authors', [])
            template['creators'] = []
            for author_str in authors:
                # Parse "Last, First" or "First Last" format
                if ',' in author_str:
                    parts = author_str.split(',', 1)
                    last = parts[0].strip()
                    first = parts[1].strip() if len(parts) > 1 else ''
                else:
                    parts = author_str.strip().split()
                    last = parts[-1] if parts else author_str
                    first = ' '.join(parts[:-1]) if len(parts) > 1 else ''

                template['creators'].append({
                    'creatorType': 'author',
                    'firstName': first,
                    'lastName': last
                })

            # Tags from topics
            template['tags'] = [{'tag': topic} for topic in topics]

            # Add custom summary to Extra field
            summary = metadata.get('summary', '')
            if summary:
                template['extra'] = f"Summary: {summary}"

            # Create item
            resp = self.zot.create_items([template])

            if resp['successful']:
                item_key = resp['successful']['0']['key']
                print(f"  ✓ Created Zotero item: {item_key}")

                # Upload PDF
                if pdf_path.exists():
                    try:
                        self.zot.attachment_simple([str(pdf_path)], item_key)
                        print(f"  ✓ Uploaded PDF attachment")
                    except Exception as e:
                        print(f"  ⚠ PDF upload failed: {e}")

                # Add to collections (one per topic)
                import time
                current_item = resp['successful']['0']

                for i, topic in enumerate(topics):
                    try:
                        coll_key = self.get_or_create_collection(topic)
                        self.zot.addto_collection(coll_key, current_item)
                        print(f"  ✓ Added to collection: {topic}")

                        # Re-fetch item with updated version before next collection
                        if i < len(topics) - 1:  # Don't re-fetch after last one
                            time.sleep(0.3)  # Small delay for API
                            current_item = self.zot.item(item_key)
                    except Exception as e:
                        print(f"  ⚠ Collection add failed for {topic}: {e}")

                return item_key

            else:
                print(f"  ✗ Failed to create Zotero item: {resp.get('failed', 'Unknown error')}")
                return None

        except Exception as e:
            print(f"  ✗ Zotero upload error: {e}")
            return None

    def _update_item_tags_collections(self, item_key: str, topics: List[str]):
        """Update tags and collections for existing item."""
        try:
            # Get current item
            item = self.zot.item(item_key)

            # Add topic tags (preserve existing tags)
            current_tags = item['data'].get('tags', [])
            current_tag_names = {tag['tag'] for tag in current_tags}

            new_tags = current_tags.copy()
            for topic in topics:
                if topic not in current_tag_names:
                    new_tags.append({'tag': topic})

            # Update item with new tags
            item['data']['tags'] = new_tags
            self.zot.update_item(item)

            # Add to collections
            for topic in topics:
                coll_key = self.get_or_create_collection(topic)
                self.zot.addto_collection(coll_key, item)

        except Exception as e:
            print(f"  ⚠ Error updating item: {e}")
