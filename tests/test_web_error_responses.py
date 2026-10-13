import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient

import web_app


class HttpErrorResponseTests(unittest.TestCase):
    def test_http_errors_carry_json_detail_for_the_frontend(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(web_app, "OUTPUT_ROOT", Path(tmp)):
            project = Path(tmp) / "review"
            project.mkdir()
            (project / "search_conditions.json").write_text(json.dumps({"project_name": "Review", "description": "LLMs"}))
            client = TestClient(web_app.create_app())
            response = client.patch("/projects/review", json={"title": "x" * 200})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json(), {"detail": "Use a conversation name between 1 and 120 characters."})
            missing = client.post("/projects/unknown/chat", json={"message": "hi"})
            self.assertEqual(missing.status_code, 404)
            self.assertIn("detail", missing.json())


if __name__ == "__main__":
    unittest.main()
