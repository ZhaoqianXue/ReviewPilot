"""Title/abstract screening instruction and response contract.

The instruction is the one evaluated in the project document (Appendix A): the confirmed review
guidance before the numbered criteria, a five-level likelihood, and exclusion only at "probably
exclude" or "definitely exclude". Two evidence fields are added so that an exclusion can be checked:
the decisive rule and a verbatim excerpt from the title or abstract. The likelihood decides; an exclusion
whose excerpt is not found in the record or whose rule is not a confirmed rule is flagged for human review.
"""
import json
import re

from .evidence_support import normalized_text

SYSTEM_PROMPT = "You screen titles and abstracts for a systematic review."
LIKELIHOODS = ("definitely exclude", "probably exclude", "uncertain", "probably include", "definitely include")
EXCLUDING = {"definitely exclude", "probably exclude"}
GUIDANCE_KEY = "screening_guidance"
OUTPUT_INSTRUCTION = (
    'Return a JSON object: {"reason": "one sentence", "likelihood": one of "definitely exclude", "probably exclude", '
    '"uncertain", "probably include", "definitely include", "criterion": "the exact text of the criterion or domain rule '
    'that decides the record, or empty", "quote": "one contiguous verbatim excerpt from the title or abstract that '
    'establishes the reason, without added quotation marks, or empty", "include": true or false}. '
    'Likelihood is how likely the record is to meet the review focus and the eligibility criteria. '
    'Set include to false only when likelihood is "probably exclude" or "definitely exclude".'
)


def _numbered(rules) -> str:
    return "\n".join(f"{number}. {rule}" for number, rule in enumerate(rules, 1))


def evidence_prompt(prompt: dict) -> dict:
    """The finalized screening instruction built from the saved criteria and the confirmed guidance."""
    eligibility = prompt.get('eligibility') or prompt.get('criteria') or {}
    inclusion = [str(rule) for rule in eligibility.get('inclusion') or []]
    exclusion = [str(rule) for rule in eligibility.get('exclusion') or []]
    guidance = str((prompt.get(GUIDANCE_KEY) or {}).get('text') or '').strip()
    template = "Screen this record for a systematic review using the eligibility criteria below.\n\n"
    if guidance:
        template += guidance + "\n"
    template += f"Inclusion criteria (the record must meet all of them):\n{_numbered(inclusion)}\n\n"
    if exclusion:
        template += f"Exclusion criteria (exclude the record if any of them applies):\n{_numbered(exclusion)}\n\n"
    template += ("Title: {title}\nAbstract: {abstract}\n\n"
                 "Include the record if it meets all inclusion criteria and no exclusion criterion. If you are unsure, include it.\n"
                 + OUTPUT_INSTRUCTION)
    return {**prompt, 'review_evidence': True, 'system_prompt': SYSTEM_PROMPT, 'user_prompt_template': template}


def _rule_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text).casefold()).strip()


_STOP = set("a an and are as at be by for from in is it its of on or that the this to with without not no only".split())
RULE_OVERLAP = 0.6


def _rules(prompt: dict) -> list[str]:
    """Every confirmed rule an exclusion may cite: the criteria and each line of the confirmed review guidance."""
    eligibility = prompt.get('eligibility') or prompt.get('criteria') or {}
    rules = [str(rule) for group in eligibility.values() for rule in (group if isinstance(group, list) else [group])]
    guidance = prompt.get(GUIDANCE_KEY) or {}
    rules += [line.strip().lstrip('-').strip() for line in str(guidance.get('text') or '').splitlines()]
    rules += [str(rule) for key in ('include_when', 'exclude_when') for rule in guidance.get(key) or []]
    return [rule for rule in dict.fromkeys(rules) if len(_rule_key(rule).split()) >= 3]


def _content_words(text: str) -> set[str]:
    return {word for word in _rule_key(text).split() if word not in _STOP and len(word) > 2}


def matched_rule(criterion: str, prompt: dict) -> str:
    """The confirmed rule a cited criterion refers to: an exact match, or one whose wording covers most of the citation.

    Models cite rules with small rewordings (a pathogen name filled in, a clause dropped), so a citation counts
    when most of its content words come from one confirmed rule.
    """
    cited = _content_words(criterion)
    if not cited:
        return ''
    rules = _rules(prompt)
    exact = {_rule_key(rule): rule for rule in rules}.get(_rule_key(criterion))
    if exact:
        return exact
    best, score = '', 0.0
    for rule in rules:
        overlap = len(cited & _content_words(rule)) / len(cited)
        if overlap > score:
            best, score = rule, overlap
    return best if score >= RULE_OVERLAP else ''


def parse_screening_response(response: str, paper: dict, prompt: dict) -> tuple[bool, dict]:
    value = json.loads(response)
    if not isinstance(value, dict) or type(value.get('include')) is not bool:
        raise ValueError('Screening response requires a boolean include decision')
    reason = str(value.get('reason') or '').strip()
    criterion = str(value.get('criterion') or '').strip()
    quote = str(value.get('quote') or '').strip()
    likelihood = str(value.get('likelihood') or '').strip().casefold()
    likelihood = likelihood if likelihood in LIKELIHOODS else ''
    source = normalized_text(str(paper.get('title') or '') + '\n' + str(paper.get('abstract') or ''))
    located = bool(quote) and normalized_text(quote) in source
    if not located and len(quote) > 2 and (quote[0], quote[-1]) in {('"', '"'), ("'", "'"), ('“', '”')}:
        excerpt = quote[1:-1].strip()
        if excerpt and normalized_text(excerpt) in source:
            quote, located = excerpt, True
    rule = matched_rule(criterion, prompt) if criterion else ''
    include = likelihood not in EXCLUDING if likelihood else value['include']
    uncertain = likelihood == 'uncertain' or value.get('uncertain') is True
    if not reason:
        include, uncertain = True, True
        reason = 'Retained for human review: the model gave no reason for its decision.'
    elif not include and (not located or not rule):
        # The likelihood decides, as in the evaluated instruction; an exclusion whose excerpt or rule
        # cannot be verified is flagged so that the reviewer checks it.
        uncertain = True
    return include, {'reason': reason, 'likelihood': likelihood, 'criterion': rule, 'quote': quote if located else '',
                     'uncertain': uncertain, 'quote_verified': located, 'criterion_matched': bool(rule),
                     'exclusion_verified': (not include) and located and bool(rule)}
