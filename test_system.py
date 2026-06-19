#!/usr/bin/env python3
"""
Quick system verification script.
Tests that all agents can be imported and instantiated.
"""

import sys
from pathlib import Path

def test_imports():
    """Test that all required modules can be imported."""
    print("Testing imports...")

    try:
        # Test utility imports
        from utils.jsonl_handler import read_jsonl, write_jsonl, append_jsonl
        from utils.human_interaction import ask_text, ask_confirm, print_header
        from utils.llm import query_llm
        from utils.downloader import PaperDownloader
        print("  ✓ Utils imports successful")

        # Test agent imports
        from agents.base_agent import BaseAgent
        from agents.search_condition_agent import SearchConditionAgent
        from agents.prompt_agent import PromptAgent
        from agents.collection_agent import CollectionAgent
        from agents.filtering_agent import FilteringAgent
        from agents.download_agent import DownloadAgent
        from agents.extraction_agent import ExtractionAgent
        from agents.coordinator import PipelineCoordinator
        print("  ✓ Agent imports successful")

        return True

    except ImportError as e:
        print(f"  ✗ Import error: {e}")
        return False


def test_agent_instantiation():
    """Test that agents can be instantiated."""
    print("\nTesting agent instantiation...")

    try:
        from agents.search_condition_agent import SearchConditionAgent
        from agents.prompt_agent import PromptAgent
        from agents.coordinator import PipelineCoordinator

        test_path = Path("output/test_project")
        test_path.mkdir(parents=True, exist_ok=True)

        # Test instantiation (without running)
        search_agent = SearchConditionAgent(test_path.parent)
        print("  ✓ SearchConditionAgent instantiated")

        prompt_agent = PromptAgent(test_path)
        print("  ✓ PromptAgent instantiated")

        coordinator = PipelineCoordinator(output_dir=test_path.parent)
        print("  ✓ PipelineCoordinator instantiated")

        return True

    except Exception as e:
        print(f"  ✗ Instantiation error: {e}")
        return False


def test_cli():
    """Test CLI is working."""
    print("\nTesting CLI...")

    try:
        import subprocess
        result = subprocess.run(
            ["python3", "cli.py", "--help"],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode == 0 and "Multi-Agent Academic Paper Search" in result.stdout:
            print("  ✓ CLI help command works")
            return True
        else:
            print("  ✗ CLI help command failed")
            return False

    except Exception as e:
        print(f"  ✗ CLI test error: {e}")
        return False


def test_config():
    """Test that config files exist."""
    print("\nTesting configuration...")

    checks = [
        ("config.py", "Main config file"),
        ("config.example.py", "Example config file"),
        ("secrets.example.txt", "Example secrets file"),
        ("requirements.txt", "Requirements file"),
    ]

    all_pass = True
    for filename, description in checks:
        if Path(filename).exists():
            print(f"  ✓ {description} exists")
        else:
            print(f"  ✗ {description} missing")
            all_pass = False

    return all_pass


def main():
    """Run all tests."""
    print("="*60)
    print("Data Scholar System Verification")
    print("="*60)

    tests = [
        ("Imports", test_imports),
        ("Agent Instantiation", test_agent_instantiation),
        ("CLI", test_cli),
        ("Configuration", test_config),
    ]

    results = {}
    for name, test_func in tests:
        results[name] = test_func()

    # Summary
    print("\n" + "="*60)
    print("Summary")
    print("="*60)

    passed = sum(results.values())
    total = len(results)

    for name, result in results.items():
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"  {status} - {name}")

    print(f"\nTests passed: {passed}/{total}")

    if passed == total:
        print("\n✓ All tests passed! System is ready to use.")
        print("\nRun the pipeline with: python3 chat.py")
        return 0
    else:
        print("\n✗ Some tests failed. Please check the errors above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
