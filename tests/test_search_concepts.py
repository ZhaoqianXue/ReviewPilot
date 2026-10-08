import unittest

from reviewpilot_core.search_concepts import blocks_from_query, build_boolean_query, derived_fields, validate_concept_blocks


def block(label, role="phenomenon", group="topic", required=True, terms=None):
    return {"label": label, "role": role, "eligibility_group": group, "required_for_eligibility": required,
            "query_terms": [label.lower()] if terms is None else terms}


class SearchConceptTests(unittest.TestCase):
    def test_alternatives_share_a_group_and_required_groups_intersect(self):
        blocks = validate_concept_blocks([
            block("Large language models (LLMs)", terms=["large language model", "LLM"]),
            block("Biomedical research", "context", "context", terms=["biomedical research"]),
            block("Clinical care", "context", "context", terms=["clinical care"]),
            block("Implementation context", "analytical_dimension", "analysis", False, []),
        ])
        self.assertEqual(build_boolean_query(blocks),
                         '("large language model" OR LLM) AND ("biomedical research" OR "clinical care")')
        fields = derived_fields(blocks)
        self.assertEqual(fields["keywords"], ["Large language models (LLMs)", "Biomedical research", "Clinical care"])
        self.assertEqual(fields["domain"], "Biomedical research, Clinical care")
        self.assertEqual(fields["extracted_concepts"]["methods"], ["Implementation context"])

    def test_validation_rejects_syntax_and_incoherent_shapes(self):
        invalid = [
            [block("Topic", role="unknown")],
            [{**block("Topic"), "required_for_eligibility": "yes"}],
            [block("Topic", terms=["topic*"])],
            [block("Topic", terms=['"topic"'])],
            [block("Topic", terms=["a AND b"])],
            [block("Topic", required=False, terms=[])],
            [{key: value for key, value in block("Topic").items() if key != "eligibility_group"}],
            [block("Topic", group="Topic group")],
            [block("Technology", group="scope"), block("Care", "context", "scope")],
            [block("Technology", group="scope"), block("Tools", group="scope", required=False)],
            [block("Topic"), block("topic", group="other")],
        ]
        for blocks in invalid:
            with self.subTest(blocks=blocks), self.assertRaises(ValueError):
                validate_concept_blocks(blocks)

    def test_legacy_population_or_context_role_stays_valid(self):
        self.assertEqual(validate_concept_blocks([block("Hospitals", "population_or_context", "setting")])[0]["role"], "population_or_context")

    def test_apostrophes_inside_words_are_plain_text(self):
        blocks = validate_concept_blocks([block("Crohn's disease", "condition", "condition", terms=["Crohn's disease"])])
        self.assertEqual(build_boolean_query(blocks), '("Crohn\'s disease")')

    def test_legacy_queries_convert_only_from_and_of_or_form(self):
        self.assertEqual([b["query_terms"] for b in blocks_from_query('LLM OR GPT')], [["LLM", "GPT"]])
        self.assertEqual(len(blocks_from_query('(a OR b) AND c AND (d)')), 3)
        for query in ("", "(a OR b) AND NOT c", "(a AND b) OR c"):
            with self.subTest(query=query), self.assertRaises(ValueError):
                blocks_from_query(query)


if __name__ == "__main__":
    unittest.main()
