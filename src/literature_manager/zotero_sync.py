"""Zotero library synchronization."""

import os
from pathlib import Path
from typing import Dict, List, Optional
from pyzotero import zotero


class ZoteroSync:
    """Handles automatic synchronization with Zotero library."""

    def __init__(self, api_key: str = None, user_id: str = None, library_type: str = None):
        """
        Initialize Zotero client.

        Args:
            api_key: Zotero API key (or from ZOTERO_API_KEY env var)
            user_id: Zotero user ID (or from ZOTERO_USER_ID env var)
            library_type: 'user' or 'group' (or from ZOTERO_LIBRARY_TYPE env var)
        """
        self.api_key = api_key or os.getenv('ZOTERO_API_KEY')
        self.user_id = user_id or os.getenv('ZOTERO_USER_ID')
        self.library_type = library_type or os.getenv('ZOTERO_LIBRARY_TYPE', 'user')

        if not self.api_key or not self.user_id:
            raise ValueError(
                "Zotero credentials not found. "
                "Set ZOTERO_API_KEY and ZOTERO_USER_ID in .env file or pass as arguments"
            )

        # Initialize Zotero client
        self.zot = zotero.Zotero(self.user_id, self.library_type, self.api_key)

        # Cache collections and DOIs (populated on first use)
        self._collections_cache = None
        self._doi_cache = None  # Maps DOI -> item key

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

    def _build_doi_cache(self):
        """Build cache of all DOIs in the library for fast duplicate checking."""
        if self._doi_cache is not None:
            return

        self._doi_cache = {}
        try:
            # Fetch all items with DOI field (paginated)
            start = 0
            limit = 100
            while True:
                items = self.zot.items(start=start, limit=limit)
                if not items:
                    break

                for item in items:
                    doi = item['data'].get('DOI', '').strip().lower()
                    if doi:
                        # Normalize DOI (remove URL prefix if present)
                        doi = doi.replace('https://doi.org/', '').replace('http://doi.org/', '')
                        self._doi_cache[doi] = item['key']

                start += limit
                if len(items) < limit:
                    break

        except Exception as e:
            print(f"  Warning: Could not build DOI cache: {e}")
            self._doi_cache = {}

    def check_exists(self, doi: Optional[str] = None, title: Optional[str] = None) -> Optional[str]:
        """
        Check if paper already exists in Zotero using DOI cache.

        Args:
            doi: Paper DOI
            title: Paper title (fallback if no DOI)

        Returns:
            Item key if exists, None otherwise
        """
        # Build DOI cache on first use
        self._build_doi_cache()

        # Check DOI cache (fast, reliable)
        if doi:
            doi_normalized = doi.strip().lower()
            doi_normalized = doi_normalized.replace('https://doi.org/', '').replace('http://doi.org/', '')
            if doi_normalized in self._doi_cache:
                return self._doi_cache[doi_normalized]

        # Fallback to title search only if no DOI
        if title and not doi:
            try:
                # Search for exact title
                results = self.zot.items(q=f'"{title}"', qmode='titleCreatorYear', limit=10)
                if results:
                    # Check if title actually matches (not just substring)
                    title_lower = title.lower().strip()
                    for item in results:
                        item_title = item['data'].get('title', '')
                        if item_title.lower().strip() == title_lower:
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
        Upload paper to Zotero with full metadata, PDF, tags, collections, and summary note.

        Args:
            metadata: Paper metadata dict (includes journal, volume, pages from CrossRef)
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

            # Publication details (from CrossRef)
            if metadata.get('journal'):
                template['publicationTitle'] = metadata['journal']
            if metadata.get('volume'):
                template['volume'] = metadata['volume']
            if metadata.get('issue'):
                template['issue'] = metadata['issue']
            if metadata.get('pages'):
                template['pages'] = metadata['pages']
            if metadata.get('issn'):
                template['ISSN'] = metadata['issn']

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

            # Add domain attributes to Extra field
            extra_parts = []
            summary = metadata.get('summary', '')
            if summary:
                extra_parts.append(f"Summary: {summary}")

            domain = metadata.get('domain_attributes', {})
            if domain.get('study_type'):
                extra_parts.append(f"Study Type: {domain['study_type']}")
            if domain.get('analytical_methods'):
                extra_parts.append(f"Methods: {', '.join(domain['analytical_methods'])}")
            if domain.get('soil_fractions'):
                extra_parts.append(f"Fractions: {', '.join(domain['soil_fractions'])}")

            if extra_parts:
                template['extra'] = '\n'.join(extra_parts)

            # Create item
            resp = self.zot.create_items([template])

            if resp['successful']:
                item_key = resp['successful']['0']['key']
                # Add to DOI cache for future duplicate checks
                if doi:
                    doi_normalized = doi.strip().lower()
                    self._doi_cache[doi_normalized] = item_key
                print(f"  ✓ Created Zotero item: {item_key}")

                # Upload PDF
                if pdf_path.exists():
                    try:
                        self.zot.attachment_simple([str(pdf_path)], item_key)
                        print(f"  ✓ Uploaded PDF attachment")
                    except Exception as e:
                        print(f"  ⚠ PDF upload failed: {e}")

                # Add summary note if we have abstract
                abstract = metadata.get('abstract')
                if abstract:
                    self._add_summary_note(item_key, metadata)

                # Add to collections (one per topic)
                current_item = resp['successful']['0']

                for topic in topics:
                    try:
                        coll_key = self.get_or_create_collection(topic)
                        # addto_collection returns updated item or True
                        result = self.zot.addto_collection(coll_key, current_item)
                        if result and isinstance(result, dict):
                            current_item = result  # Use returned version
                        print(f"  ✓ Added to collection: {topic}")
                    except Exception as e:
                        print(f"  ⚠ Collection add failed for {topic}: {e}")

                return item_key

            else:
                print(f"  ✗ Failed to create Zotero item: {resp.get('failed', 'Unknown error')}")
                return None

        except Exception as e:
            print(f"  ✗ Zotero upload error: {e}")
            return None

    def _add_summary_note(self, parent_key: str, metadata: Dict):
        """
        Add an enhanced summary note to the Zotero item.

        Creates a note with main finding, key approach, implications,
        and structured research details.
        """
        try:
            # Build note content
            enhanced = metadata.get('enhanced_summary', {})
            domain = metadata.get('domain_attributes', {})
            short_summary = metadata.get('summary', '')

            note_parts = ["<h2>📋 Paper Summary</h2>"]

            # Enhanced summary (main finding, approach, implication)
            if enhanced:
                if enhanced.get('main_finding'):
                    note_parts.append(f"<h3>Main Finding</h3>")
                    note_parts.append(f"<p>{enhanced['main_finding']}</p>")

                if enhanced.get('key_approach'):
                    note_parts.append(f"<h3>Key Approach</h3>")
                    note_parts.append(f"<p>{enhanced['key_approach']}</p>")

                if enhanced.get('implication'):
                    note_parts.append(f"<h3>Implication</h3>")
                    note_parts.append(f"<p>{enhanced['implication']}</p>")
            elif short_summary:
                # Fallback to short summary if no enhanced summary
                note_parts.append(f"<p><strong>Key Finding:</strong> {short_summary}</p>")

            # Domain attributes section
            if domain and any(domain.values()):
                note_parts.append("<h3>Research Details</h3>")
                note_parts.append("<ul>")

                if domain.get('study_type'):
                    note_parts.append(f"<li><strong>Study Type:</strong> {domain['study_type']}</li>")
                if domain.get('ecosystem'):
                    note_parts.append(f"<li><strong>Ecosystem:</strong> {domain['ecosystem']}</li>")
                if domain.get('analytical_methods'):
                    methods = ', '.join(domain['analytical_methods'])
                    note_parts.append(f"<li><strong>Methods:</strong> {methods}</li>")
                if domain.get('soil_fractions'):
                    fractions = ', '.join(domain['soil_fractions'])
                    note_parts.append(f"<li><strong>Soil Fractions:</strong> {fractions}</li>")
                if domain.get('soil_properties'):
                    props = ', '.join(domain['soil_properties'])
                    note_parts.append(f"<li><strong>Properties Measured:</strong> {props}</li>")
                if domain.get('management'):
                    mgmt = ', '.join(domain['management'])
                    note_parts.append(f"<li><strong>Management:</strong> {mgmt}</li>")
                if domain.get('depth_info'):
                    depths = ', '.join(domain['depth_info']) if isinstance(domain['depth_info'], list) else str(domain['depth_info'])
                    note_parts.append(f"<li><strong>Sampling Depths:</strong> {depths}</li>")

                note_parts.append("</ul>")

            note_parts.append("<hr><p><em>Generated by Literature Manager</em></p>")

            note_content = '\n'.join(note_parts)

            # Create note
            note_template = self.zot.item_template('note')
            note_template['note'] = note_content
            note_template['parentItem'] = parent_key

            resp = self.zot.create_items([note_template])

            if resp['successful']:
                print(f"  ✓ Added summary note")
            else:
                print(f"  ⚠ Note creation failed")

        except Exception as e:
            print(f"  ⚠ Note creation error: {e}")

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

    def update_citation_metadata(
        self,
        doi: str,
        journal: Optional[str] = None,
        volume: Optional[str] = None,
        issue: Optional[str] = None,
        pages: Optional[str] = None,
    ) -> bool:
        """
        Update citation metadata for an existing Zotero item.

        Args:
            doi: DOI to match the item
            journal: Journal name (publicationTitle)
            volume: Volume number
            issue: Issue number
            pages: Page range

        Returns:
            True if updated successfully, False otherwise
        """
        try:
            # Find item by DOI
            self._build_doi_cache()
            doi_normalized = doi.strip().lower()
            doi_normalized = doi_normalized.replace('https://doi.org/', '').replace('http://doi.org/', '')

            item_key = self._doi_cache.get(doi_normalized)
            if not item_key:
                return False

            # Get current item
            item = self.zot.item(item_key)

            # Track if we made changes
            changed = False

            # Only update empty fields
            if journal and not item['data'].get('publicationTitle'):
                item['data']['publicationTitle'] = journal
                changed = True
            if volume and not item['data'].get('volume'):
                item['data']['volume'] = volume
                changed = True
            if issue and not item['data'].get('issue'):
                item['data']['issue'] = issue
                changed = True
            if pages and not item['data'].get('pages'):
                item['data']['pages'] = pages
                changed = True

            if changed:
                self.zot.update_item(item)
                return True

            return False

        except Exception as e:
            print(f"  ⚠ Error updating citation: {e}")
            return False

    def add_or_update_fulltext_note(
        self,
        doi: str,
        fulltext_summary: Dict,
        title: str = ""
    ) -> bool:
        """
        Add or update a fulltext summary note on a Zotero item.

        Args:
            doi: DOI to match the item
            fulltext_summary: Dict with main_finding, key_approach, key_results, implication
            title: Paper title (for display purposes)

        Returns:
            True if note was created/updated, False otherwise
        """
        try:
            # Find item by DOI
            self._build_doi_cache()
            doi_normalized = doi.strip().lower()
            doi_normalized = doi_normalized.replace('https://doi.org/', '').replace('http://doi.org/', '')

            item_key = self._doi_cache.get(doi_normalized)
            if not item_key:
                return False

            # Check for existing summary note
            children = self.zot.children(item_key)
            existing_note_key = None

            for child in children:
                if child['data'].get('itemType') == 'note':
                    note_content = child['data'].get('note', '')
                    if 'Paper Summary' in note_content or 'Main Finding' in note_content:
                        existing_note_key = child['key']
                        break

            # Build note HTML
            note_parts = ["<h2>📋 Paper Summary</h2>"]

            if fulltext_summary.get('main_finding'):
                note_parts.append("<h3>Main Finding</h3>")
                note_parts.append(f"<p>{fulltext_summary['main_finding']}</p>")

            if fulltext_summary.get('key_approach'):
                note_parts.append("<h3>Key Approach</h3>")
                note_parts.append(f"<p>{fulltext_summary['key_approach']}</p>")

            if fulltext_summary.get('key_results'):
                note_parts.append("<h3>Key Results</h3>")
                note_parts.append(f"<p>{fulltext_summary['key_results']}</p>")

            if fulltext_summary.get('implication'):
                note_parts.append("<h3>Implication</h3>")
                note_parts.append(f"<p>{fulltext_summary['implication']}</p>")

            note_parts.append("<hr><p><em>Generated by Literature Manager (fulltext)</em></p>")

            note_content = '\n'.join(note_parts)

            if existing_note_key:
                # Update existing note
                note_item = self.zot.item(existing_note_key)
                note_item['data']['note'] = note_content
                self.zot.update_item(note_item)
                return True
            else:
                # Create new note
                note_template = self.zot.item_template('note')
                note_template['note'] = note_content
                note_template['parentItem'] = item_key

                resp = self.zot.create_items([note_template])

                if resp['successful']:
                    return True
                else:
                    return False

        except Exception as e:
            print(f"  ⚠ Error adding fulltext note: {e}")
            return False
