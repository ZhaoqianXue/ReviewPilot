import unittest

from searchers.pubmed import PubMedSearcher


def article_xml(abstract: str) -> str:
    return (
        "<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>101</PMID><Article>"
        "<ArticleTitle>Lassa fever transmission</ArticleTitle>"
        f"{abstract}"
        "<AuthorList><Author><LastName>Okafor</LastName><ForeName>Ada</ForeName></Author></AuthorList>"
        "<Journal><Title>Epidemics</Title><JournalIssue><PubDate><Year>2024</Year><Month>Mar</Month></PubDate></JournalIssue></Journal>"
        "</Article><OtherAbstract Language=\"fre\"><AbstractText>Résumé en français.</AbstractText></OtherAbstract></MedlineCitation>"
        "<PubmedData><ArticleIdList><ArticleId IdType=\"doi\">10.1000/lassa</ArticleId></ArticleIdList></PubmedData>"
        "</PubmedArticle></PubmedArticleSet>"
    )


class PubMedAbstractParsingTests(unittest.TestCase):
    def parse(self, abstract: str) -> dict:
        articles = PubMedSearcher(email="fixture@example.org")._parse_xml(article_xml(abstract))
        self.assertEqual(len(articles), 1)
        return articles[0]

    def test_structured_abstract_keeps_every_labelled_section(self):
        article = self.parse(
            "<Abstract>"
            "<AbstractText Label=\"BACKGROUND\" NlmCategory=\"BACKGROUND\">Lassa virus is endemic.</AbstractText>"
            "<AbstractText Label=\"METHODS\">We fitted a <i>stochastic</i>\n  SEIR model.</AbstractText>"
            "<AbstractText Label=\"RESULTS\"></AbstractText>"
            "<AbstractText Label=\"CONCLUSIONS\">Rodent control reduced spillover.</AbstractText>"
            "<CopyrightInformation>© 2024 The Authors.</CopyrightInformation>"
            "</Abstract>"
        )
        self.assertEqual(
            article["abstract"],
            "BACKGROUND: Lassa virus is endemic.\n"
            "METHODS: We fitted a stochastic SEIR model.\n"
            "CONCLUSIONS: Rodent control reduced spillover.",
        )
        self.assertEqual((article["id"], article["doi"], article["publication_date"]), ("101", "10.1000/lassa", "2024-03"))

    def test_unstructured_and_missing_abstracts(self):
        self.assertEqual(self.parse("<Abstract><AbstractText>One paragraph abstract.</AbstractText></Abstract>")["abstract"],
                         "One paragraph abstract.")
        self.assertEqual(self.parse("")["abstract"], "")


if __name__ == "__main__":
    unittest.main()
