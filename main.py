#!/usr/bin/env python3
"""
Academic Search Tool - Search across multiple academic databases
"""

import argparse
import json
import csv
import os
from datetime import datetime
from typing import List, Dict, Optional

# Import search modules
from searchers import pubmed
from searchers import wos
from searchers import scopus
from searchers import openalex
from searchers import google_scholar
from searchers import arxiv_search
from searchers import dblp

# Import API keys from config
try:
    import config
except ImportError:
    config = None


# Available platforms configuration
PLATFORMS = {
    "pubmed": {
        "module": pubmed,
        "name": "PubMed",
        "requires_key": False,
        "key_env": None,
        "description": "Biomedical literature from MEDLINE and life science journals"
    },
    "wos": {
        "module": wos,
        "name": "Web of Science",
        "requires_key": True,
        "key_env": "WOS_API_KEY",
        "description": "Multidisciplinary research from Clarivate"
    },
    "scopus": {
        "module": scopus,
        "name": "Scopus",
        "requires_key": True,
        "key_env": "SCOPUS_API_KEY",
        "description": "Abstract and citation database from Elsevier"
    },
    "openalex": {
        "module": openalex,
        "name": "OpenAlex",
        "requires_key": False,
        "key_env": None,
        "description": "Open catalog of scholarly works, authors, venues"
    },
    "google_scholar": {
        "module": google_scholar,
        "name": "Google Scholar",
        "requires_key": False,
        "key_env": None,
        "description": "Google's academic search (may be rate-limited)"
    },
    "arxiv": {
        "module": arxiv_search,
        "name": "arXiv",
        "requires_key": False,
        "key_env": None,
        "description": "Open-access preprints in physics, math, CS, and more"
    },
    "dblp": {
        "module": dblp,
        "name": "DBLP",
        "requires_key": False,
        "key_env": None,
        "description": "DBLP - CS Conferences & Journals (NeurIPS, ICML, CVPR, ACL, etc.)"
    }
}


class AcademicSearcher:
    def __init__(self, user_config: Optional[Dict] = None):
        """
        Initialize the academic searcher.

        Args:
            user_config: Optional configuration dictionary with API keys and settings
        """
        self.user_config = user_config or {}

        # Load API keys from config file, then environment, then user_config
        self.api_keys = {
            "wos": self._get_config_value("WOS_API_KEY", "wos_api_key"),
            "scopus": self._get_config_value("SCOPUS_API_KEY", "scopus_api_key"),
            "pubmed": self._get_config_value("PUBMED_API_KEY", "pubmed_api_key"),
            "openalex": self._get_config_value("OPENALEX_API_KEY", "openalex_api_key"),
        }
        self.last_errors = {}

        self.email = self._get_config_value("EMAIL", "email") or os.getenv("RESEARCHER_EMAIL", "researcher@example.com")

    def _get_config_value(self, config_attr: str, user_config_key: str) -> Optional[str]:
        """Get config value from config file, environment, or user_config."""
        # First check config.py
        if config and hasattr(config, config_attr):
            value = getattr(config, config_attr)
            if value:
                return value
        # Then check environment variable
        env_value = os.getenv(config_attr)
        if env_value:
            return env_value
        # Finally check user_config dict
        return self.user_config.get(user_config_key)

    def search(self, query: str, platforms: List[str], max_results: int = 100,
               output_folder: Optional[str] = None, **kwargs) -> Dict[str, List[Dict]]:
        """
        Search across multiple platforms.

        Args:
            query: Search term
            platforms: List of platform keys to search
            max_results: Maximum results per platform (0 = unlimited)
            output_folder: Optional folder path for real-time saving (JSONL per platform)
            **kwargs: Additional platform-specific arguments
                - arxiv_query: Optional separate query for arXiv
                - arxiv_categories: arXiv category filters
                - cs_venues: CS conference venue filters
                - use_proxy: Use proxy for Google Scholar

        Returns:
            Dictionary mapping platform names to result lists
        """
        # Handle unlimited (0 or None) by using a very large number
        if max_results is None or max_results == 0:
            max_results = 100000

        # Create output folder if specified
        if output_folder:
            os.makedirs(output_folder, exist_ok=True)

        results = {}
        self.last_errors = {}

        for platform in platforms:
            if platform not in PLATFORMS:
                print(f"Warning: Unknown platform '{platform}', skipping...")
                continue

            platform_info = PLATFORMS[platform]
            print(f"\nSearching {platform_info['name']}...")

            # Build output file path for this platform
            output_file = None
            if output_folder:
                output_file = os.path.join(output_folder, f"{platform}.jsonl")

            try:
                if platform == "pubmed":
                    results[platform] = pubmed.search(
                        query,
                        max_results=max_results,
                        email=self.email,
                        api_key=self.api_keys.get("pubmed"),
                        output_file=output_file,
                        **({"date_range": kwargs["date_range"]} if kwargs.get("date_range") else {})
                    )

                elif platform == "wos":
                    if not self.api_keys.get("wos"):
                        print(f"  Skipping: WOS_API_KEY not set")
                        continue
                    results[platform] = wos.search(
                        query,
                        max_results=max_results,
                        api_key=self.api_keys["wos"]
                    )

                elif platform == "scopus":
                    if not self.api_keys.get("scopus"):
                        print(f"  Skipping: SCOPUS_API_KEY not set")
                        continue
                    results[platform] = scopus.search(
                        query,
                        max_results=max_results,
                        api_key=self.api_keys["scopus"]
                    )

                elif platform == "openalex":
                    results[platform] = openalex.search(
                        query,
                        max_results=max_results,
                        email=self.email,
                        api_key=self.api_keys.get("openalex"),
                        output_file=output_file,
                        **({"date_range": kwargs["date_range"]} if kwargs.get("date_range") else {})
                    )

                elif platform == "google_scholar":
                    use_proxy = kwargs.get("use_proxy", False)
                    results[platform] = google_scholar.search(
                        query,
                        max_results=max_results,
                        use_proxy=use_proxy
                    )

                elif platform == "arxiv":
                    # Use arxiv-specific query if provided, otherwise use main query
                    arxiv_query = kwargs.get("arxiv_query", query)
                    categories = kwargs.get("arxiv_categories")
                    results[platform] = arxiv_search.search(
                        arxiv_query,
                        max_results=max_results,
                        categories=categories,
                        output_file=output_file,
                        **({"date_range": kwargs["date_range"]} if kwargs.get("date_range") else {})
                    )

                elif platform == "dblp":
                    venues = kwargs.get("cs_venues")
                    results[platform] = dblp.search(
                        query,
                        max_results=max_results,
                        venues=venues
                    )

                print(f"  Found {len(results.get(platform, []))} results")

            except Exception as e:
                print(f"  Error searching {platform}: {e}")
                self.last_errors[platform] = str(e)
                results[platform] = []

        return results

    def save_results(self, results: Dict[str, List[Dict]], output_path: str,
                     format: str = "json", save_by_platform: bool = False):
        """
        Save search results to file.

        Args:
            results: Dictionary of results by platform
            output_path: Output file path or folder path
            format: Output format ('json', 'csv', 'txt', or 'jsonl')
            save_by_platform: If True, save each platform to separate JSONL files
        """
        if save_by_platform:
            self._save_by_platform(results, output_path)
        elif format == "json":
            self._save_json(results, output_path)
        elif format == "csv":
            self._save_csv(results, output_path)
        elif format == "txt":
            self._save_txt(results, output_path)
        elif format == "jsonl":
            self._save_jsonl(results, output_path)
        else:
            raise ValueError(f"Unknown format: {format}")

        if not save_by_platform:
            print(f"\nResults saved to: {output_path}")

    def _save_json(self, results: Dict, output_path: str):
        """Save results as JSON."""
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)

    def _save_csv(self, results: Dict, output_path: str):
        """Save results as CSV."""
        # Flatten all results
        all_results = []
        for platform, articles in results.items():
            for article in articles:
                article_copy = article.copy()
                # Convert lists to strings for CSV
                if "authors" in article_copy:
                    article_copy["authors"] = "; ".join(article_copy["authors"])
                if "categories" in article_copy:
                    article_copy["categories"] = "; ".join(article_copy["categories"])
                all_results.append(article_copy)

        if not all_results:
            print("No results to save")
            return

        # Get all possible fields
        fieldnames = set()
        for article in all_results:
            fieldnames.update(article.keys())
        fieldnames = sorted(fieldnames)

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_results)

    def _save_txt(self, results: Dict, output_path: str):
        """Save results as formatted text."""
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(f"Academic Search Results\n")
            f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("=" * 80 + "\n\n")

            total = 0
            for platform, articles in results.items():
                platform_name = PLATFORMS.get(platform, {}).get("name", platform)
                f.write(f"\n{'=' * 40}\n")
                f.write(f"{platform_name} ({len(articles)} results)\n")
                f.write(f"{'=' * 40}\n\n")

                for i, article in enumerate(articles, 1):
                    f.write(f"{i}. {article.get('title', 'No title')}\n")
                    f.write(f"   Authors: {', '.join(article.get('authors', []))}\n")
                    f.write(f"   Year: {article.get('year', 'N/A')}\n")
                    f.write(f"   Journal/Venue: {article.get('journal', 'N/A')}\n")
                    if article.get("doi"):
                        f.write(f"   DOI: {article['doi']}\n")
                    if article.get("url"):
                        f.write(f"   URL: {article['url']}\n")
                    f.write("\n")

                total += len(articles)

            f.write(f"\n{'=' * 80}\n")
            f.write(f"Total results: {total}\n")

    def _save_jsonl(self, results: Dict, output_path: str):
        """Save all results as JSONL (one JSON object per line)."""
        with open(output_path, "w", encoding="utf-8") as f:
            for platform, articles in results.items():
                for article in articles:
                    article_with_platform = {"platform": platform, **article}
                    f.write(json.dumps(article_with_platform, ensure_ascii=False) + "\n")

    def _save_by_platform(self, results: Dict, output_folder: str):
        """Save each platform to a separate JSONL file in a folder."""
        # Create output folder if it doesn't exist
        os.makedirs(output_folder, exist_ok=True)

        print(f"\nSaving results to folder: {output_folder}")

        for platform, articles in results.items():
            if not articles:
                continue

            output_file = os.path.join(output_folder, f"{platform}.jsonl")
            with open(output_file, "w", encoding="utf-8") as f:
                for article in articles:
                    f.write(json.dumps(article, ensure_ascii=False) + "\n")

            print(f"  {platform}: {len(articles)} results -> {output_file}")

        # Also save a summary file
        summary_file = os.path.join(output_folder, "summary.json")
        summary = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_results": sum(len(articles) for articles in results.values()),
            "platforms": {
                platform: len(articles)
                for platform, articles in results.items()
            }
        }
        with open(summary_file, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)

        print(f"  summary -> {summary_file}")


def main():
    parser = argparse.ArgumentParser(
        description="Search academic databases for scholarly articles",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Search all free platforms
  python main.py "machine learning healthcare" --platforms openalex arxiv pubmed

  # Search with specific CS venues
  python main.py "transformer" --platforms dblp --cs-venues neurips icml iclr

  # Search arXiv in specific categories
  python main.py "neural network" --platforms arxiv --arxiv-categories cs.LG cs.AI

  # Save as CSV
  python main.py "deep learning" --platforms openalex --output results.csv --format csv

Available platforms:
  pubmed         - PubMed (free, needs email)
  wos            - Web of Science (requires WOS_API_KEY)
  scopus         - Scopus (requires SCOPUS_API_KEY)
  openalex       - OpenAlex (free)
  google_scholar - Google Scholar (free, may be rate-limited)
  arxiv          - arXiv (free)
  dblp           - DBLP CS Conferences & Journals (free)

Environment variables:
  WOS_API_KEY      - Web of Science API key
  SCOPUS_API_KEY   - Scopus API key
  PUBMED_API_KEY   - PubMed API key (optional, for higher rate limits)
  RESEARCHER_EMAIL - Your email for API identification
        """
    )

    parser.add_argument(
        "query",
        nargs="?",
        default=None,
        help="Search query (uses SEARCH_CONDITIONS from config.py if not provided)"
    )
    parser.add_argument(
        "--platforms", "-p",
        nargs="+",
        choices=list(PLATFORMS.keys()),
        default=None,
        help="Platforms to search (uses FOCUS_PLATFORMS from config.py if not provided)"
    )
    parser.add_argument(
        "--max-results", "-n",
        type=int,
        default=0,
        help="Maximum results per platform (default: 0 = unlimited)"
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output file path (default: results_<timestamp>.<format>)"
    )
    parser.add_argument(
        "--format", "-f",
        choices=["json", "csv", "txt", "jsonl"],
        default="json",
        help="Output format (default: json)"
    )
    parser.add_argument(
        "--save-by-platform",
        action="store_true",
        help="Save each platform to separate JSONL files in a folder"
    )
    parser.add_argument(
        "--search-scope",
        choices=["title_abstract", "full_text"],
        default=None,
        help="Search scope: title_abstract or full_text (uses SEARCH_SCOPE from config.py if not provided)"
    )
    parser.add_argument(
        "--email",
        default=None,
        help="Your email for API identification"
    )
    parser.add_argument(
        "--arxiv-categories",
        nargs="+",
        default=None,
        help="arXiv categories to filter (e.g., cs.LG cs.AI)"
    )
    parser.add_argument(
        "--cs-venues",
        nargs="+",
        default=None,
        help="CS conference venues (e.g., neurips icml cvpr)"
    )
    parser.add_argument(
        "--use-proxy",
        action="store_true",
        help="Use proxy for Google Scholar (slower but avoids blocks)"
    )
    parser.add_argument(
        "--list-venues",
        action="store_true",
        help="List available CS conference venues and exit"
    )

    args = parser.parse_args()

    # List venues if requested
    if args.list_venues:
        print("Available DBLP Venues:")
        print("-" * 40)
        venues = dblp.list_venues()
        for key, value in sorted(venues.items()):
            print(f"  {key:20s} -> {value}")
        return

    # Load from config.py if arguments not provided
    query = args.query
    platforms = args.platforms

    # Import config module for search settings
    try:
        import config as cfg
    except ImportError:
        cfg = None

    if query is None:
        if cfg and hasattr(cfg, 'SEARCH_CONDITIONS'):
            query = cfg.SEARCH_CONDITIONS
        else:
            print("Error: No query provided and SEARCH_CONDITIONS not found in config.py")
            return

    if platforms is None:
        if cfg and hasattr(cfg, 'FOCUS_PLATFORMS'):
            platforms = cfg.FOCUS_PLATFORMS
        else:
            platforms = ["pubmed", "arxiv", "openalex"]

    # Load search scope from config or args
    search_scope = args.search_scope
    if search_scope is None:
        if cfg and hasattr(cfg, 'SEARCH_SCOPE'):
            search_scope = cfg.SEARCH_SCOPE
        else:
            search_scope = "title_abstract"

    # Load output folder from config
    output_folder = None
    if cfg and hasattr(cfg, 'OUTPUT_FOLDER'):
        output_folder = cfg.OUTPUT_FOLDER

    # Load arXiv-specific query from config if available
    arxiv_query = None
    if cfg and hasattr(cfg, 'arXiv_SEARCH_CONDITIONS'):
        arxiv_query = cfg.arXiv_SEARCH_CONDITIONS

    # Initialize searcher
    user_config = {}
    if args.email:
        user_config["email"] = args.email

    searcher = AcademicSearcher(user_config)

    # Perform search
    print(f"Searching for: '{query}'")
    if arxiv_query and "arxiv" in platforms:
        print(f"arXiv query: '{arxiv_query}'")
    print(f"Platforms: {', '.join(platforms)}")
    print(f"Search scope: {search_scope}")
    print(f"Max results per platform: {'unlimited' if args.max_results == 0 else args.max_results}")

    results = searcher.search(
        query,
        platforms=platforms,
        max_results=args.max_results,
        arxiv_query=arxiv_query,
        arxiv_categories=args.arxiv_categories,
        cs_venues=args.cs_venues,
        use_proxy=args.use_proxy
    )

    # Count total results
    total = sum(len(r) for r in results.values())
    print(f"\nTotal results: {total}")

    # Save results - default to folder with separate JSONL files per platform
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if args.output:
        # User specified output path
        output_path = args.output
        searcher.save_results(results, output_path, args.format, save_by_platform=args.save_by_platform)
    else:
        # Default: save to folder with separate JSONL files per platform
        if output_folder:
            folder_path = f"{output_folder}_{timestamp}"
        else:
            folder_path = f"search_results_{timestamp}"

        searcher.save_results(results, folder_path, args.format, save_by_platform=True)


if __name__ == "__main__":
    main()
