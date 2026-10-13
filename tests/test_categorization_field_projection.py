import unittest

from reviewpilot_core.state_projection import _categorization_workflow


class CategorizationFieldProjectionTests(unittest.TestCase):
    def test_retrieval_metadata_and_raw_response_are_not_evidence_fields(self):
        schema = {"fields": [
            {"name": "methods", "type": "string"},
            {"name": "key_findings", "type": "string"},
        ]}
        row = {
            "title": "Paper A",
            "extraction_status": "success",
            "methods": "Interview study",
            "key_findings": "Reported findings",
            "extracted_text": '{"methods": "Interview study"}',
            "pdf_failure_class": "article_print_failed",
            "retrieval_status": "unavailable",
            "web_search_fallback_pending": False,
        }

        workflow = _categorization_workflow(schema, [row], {}, {}, [])

        self.assertEqual(workflow["metrics"], {"papersExtracted": 1, "papersInAnalysis": 1, "fields": 2, "papersWithoutCategory": 0})
        self.assertEqual(workflow["fieldNames"], ["methods", "key_findings"])
        self.assertEqual(workflow["fullResults"]["columns"], ["title", "methods", "key_findings"])
        self.assertEqual(set(workflow["fieldProfiles"]), {"methods", "key_findings"})

    def test_schema_less_results_keep_substantive_fields_only(self):
        workflow = _categorization_workflow({}, [{
            "methods": "Survey",
            "extracted_text": "Raw response",
            "retrieval_status": "unavailable",
            "pdf_failure_class": "paywall",
            "web_search_fallback_pending": True,
        }], {}, {}, [])

        self.assertEqual(workflow["fieldNames"], ["methods"])
        self.assertEqual(workflow["metrics"]["fields"], 1)
