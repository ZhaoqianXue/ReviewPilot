"""Slow agent calls must leave the shared ASGI event loop responsive."""

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event
from types import SimpleNamespace
from unittest.mock import patch

from starlette.testclient import TestClient

import web_app


class WebResponsivenessTests(unittest.TestCase):
    def test_dangling_symlink_reserves_its_project_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reserved = root / "same-title"
            reserved.symlink_to(root / "missing-target", target_is_directory=True)
            self.assertEqual(web_app._unique_project_id(root, "same-title"), "same-title-2")
            self.assertTrue(reserved.is_symlink())

    def test_slow_agent_requests_do_not_block_other_requests(self):
        for method, path, target, result in (
            ("post", "/projects", "create_project", {"id": "demo"}),
            ("put", "/projects/demo/setup", "update_project_setup", {"id": "demo"}),
            ("post", "/projects/demo/chat", "LeadAgent.handle_message",
             SimpleNamespace(reply="Reply", to_dict=lambda: {})),
        ):
            with self.subTest(path=path), tempfile.TemporaryDirectory() as directory:
                entered, release = Event(), Event()

                def slow_call(*args, **kwargs):
                    entered.set()
                    release.wait(5)
                    return result

                with patch.object(web_app, "OUTPUT_ROOT", Path(directory)), \
                     patch.object(web_app, "known_project", return_value=True), \
                     patch.object(web_app, "build_project_state", return_value={}), \
                     patch(f"web_app.{target}", side_effect=slow_call), \
                     TestClient(web_app.create_app()) as client, \
                     ThreadPoolExecutor(max_workers=2) as pool:
                    pending = pool.submit(getattr(client, method), path, json={"message": "Hello"})
                    try:
                        self.assertTrue(entered.wait(2), "Agent request did not start")
                        probe = pool.submit(client.get, "/favicon.ico")
                        self.assertEqual(probe.result(timeout=1).status_code, 204)
                    finally:
                        release.set()
                        self.assertIn(pending.result(timeout=3).status_code, (200, 201))

    def test_concurrent_same_name_creations_use_distinct_project_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            both_started = Barrier(2)

            def setup(output_root, project_id, config):
                both_started.wait(timeout=3)
                return {**config, "project_path": str(Path(output_root) / project_id)}

            with patch.object(web_app, "_run_lead_agent_search_setup", side_effect=setup), \
                 ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(web_app.create_project, root,
                    {"project_name": "Same title", "description": "Review topic"}) for _ in range(2)]
                results = [future.result(timeout=5) for future in futures]
            self.assertEqual(len({result["id"] for result in results}), 2)
            for result in results:
                self.assertTrue((root / result["id"] / "search_conditions.json").is_file())
