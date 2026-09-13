"""Minimal Boolean query tree, preserving grouping and quoted operator words."""
import re


def tokens(text):
    result = []; buffer = []; quote = ''; i = 0
    def flush():
        value = ''.join(buffer).strip()
        if value:
            result.append(('term', value))
        buffer.clear()
    while i < len(text):
        char = text[i]
        if quote:
            buffer.append(char)
            if char == quote and (i == 0 or text[i-1] != '\\'):
                quote = ''
            i += 1; continue
        if char == "'" and i > 0 and text[i-1].isalnum():
            buffer.append(char); i += 1; continue
        if char in '\"\'':
            quote = char; buffer.append(char); i += 1; continue
        match = re.match(r'\b(ANDNOT|AND|OR|NOT)\b', text[i:], re.I) if i == 0 or not text[i-1].isalnum() else None
        if char in '()' or match:
            flush()
            value = match.group(0).upper() if match else char
            result.append((value, value)); i += len(value)
        else:
            buffer.append(char); i += 1
    if quote:
        raise ValueError('Unclosed quote in Boolean query.')
    flush()
    return result


def parse(text):
    stream = tokens(text); index = 0
    def peek():
        return stream[index][0] if index < len(stream) else None
    def atom():
        nonlocal index
        token = peek()
        if token == '(':
            index += 1; node = expression()
            if peek() != ')': raise ValueError('Unbalanced query parentheses.')
            index += 1; return node
        if token == 'NOT':
            index += 1; return ('NOT', atom())
        if token != 'term': raise ValueError('Expected a search term in Boolean query.')
        value = stream[index]; index += 1; return value
    def conjunction():
        nonlocal index
        node = atom()
        while peek() in {'AND', 'ANDNOT', 'NOT'}:
            op = peek(); index += 1
            node = ('ANDNOT' if op in {'NOT','ANDNOT'} else op, node, atom())
        return node
    def expression():
        nonlocal index
        node = conjunction()
        while peek() == 'OR':
            index += 1; node = ('OR', node, conjunction())
        return node
    if not stream: raise ValueError('Search query must not be empty.')
    node = expression()
    if index != len(stream): raise ValueError('Invalid Boolean query grouping.')
    return node


def render(node, term, *, negative='NOT'):
    if node[0] == 'term': return term(node[1])
    if node[0] == 'AND' and node[2][0] == 'NOT':
        node = ('ANDNOT', node[1], node[2][1])
    if node[0] == 'NOT' and negative == 'ANDNOT':
        raise ValueError('arXiv negation requires a positive query followed by ANDNOT.')
    if node[0] == 'NOT': return f'NOT ({render(node[1], term, negative=negative)})'
    op = negative if node[0] == 'ANDNOT' else node[0]
    return f'({render(node[1], term, negative=negative)} {op} {render(node[2], term, negative=negative)})'


def phrase(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in '\"\'':
        return '"' + value[1:-1] + '"'
    return '"' + value + '"' if ' ' in value else value


def conjunctive_groups(node):
    """Only expand an actual AND of OR terms; never flatten mixed nesting."""
    def alternatives(item):
        if item[0] == 'term': return [item[1].strip('\"\'')]
        if item[0] != 'OR': raise ValueError()
        return alternatives(item[1]) + alternatives(item[2])
    def groups(item):
        if item[0] == 'AND': return groups(item[1]) + groups(item[2])
        return [alternatives(item)]
    try:
        result = groups(node)
        return tuple(result) if len(result) > 1 else None
    except ValueError:
        return None
