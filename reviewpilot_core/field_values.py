"""One value contract for model extraction and human corrections.

None and the empty string mean not reported, including for required fields.
"""
import math
import json
import re


def kind(field):
    raw = str(field.get('type') or 'Text').strip().lower()
    aliases = {'text':'string','long text':'string','str':'string','categorical':'string','select':'string',
               'int':'integer','float':'number','double':'number','list':'array','text (list)':'text_array',
               'list[str]':'text_array','list[string]':'text_array','dict':'object','bool':'boolean'}
    return aliases.get(raw, raw)


# Loose spellings of a list of short texts that models write for the schema type; the schema's own label is "Text (list)".
_TEXT_LIST_SPELLINGS = {'text list', 'list of text', 'list of texts', 'text array', 'array of text', 'array of strings',
                        'list of strings', 'string list', 'list (text)', 'text[]', 'list[text]'}


def field_type_label(value):
    """A schema type label, with loose spellings of a text list (e.g. "Text list") written as "Text (list)"."""
    text = re.sub(r'\s+', ' ', str(value or '')).strip()
    return 'Text (list)' if text.lower() in _TEXT_LIST_SPELLINGS else text


def validate_value(field, value):
    try:
        json.dumps(value, allow_nan=False)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError('Field values must be valid JSON with finite numbers.') from exc
    if value is None or value == '':
        return value
    k = kind(field)
    checks = {'string': lambda: isinstance(value,str), 'integer': lambda: type(value) is int,
              'number': lambda: type(value) in {int,float} and math.isfinite(value),
              'array': lambda: isinstance(value,list), 'text_array': lambda: isinstance(value,list) and all(isinstance(v,str) for v in value),
              'object': lambda: isinstance(value,dict), 'boolean': lambda: type(value) is bool}
    if k not in checks or not checks[k]():
        raise ValueError(f"Field {field.get('name', '')} must match type {field.get('type', 'Text')}.")
    options = field.get('enum', field.get('options'))
    if options is not None:
        if not isinstance(options,list) or not options:
            raise ValueError('Field enum/options must be a nonempty list.')
        if any(v not in options for v in (value if k in {'array','text_array'} else [value])):
            raise ValueError(f"Field {field.get('name', '')} must use the declared enum/options.")
    if isinstance(value,list) and isinstance(field.get('items'),dict):
        for v in value:
            validate_value({**field['items'], 'name':field.get('name','')+' item'}, v)
    return value


def _norm(text):
    text = text.replace('–', '-').replace('—', '-').replace('“', '"').replace('”', '"').replace('’', "'")
    return re.sub(r'\s+', ' ', text).strip().lower()


def coerce_value(field, value):
    """Bring a model value to the field's declared type when the intended value is unambiguous.

    A list field given one string becomes a list split at semicolons or line breaks, a whole-number string in a
    number field becomes a number, and an option written with different case or spacing becomes the declared
    option. A value that names something else is left for validate_value to reject.
    """
    if value is None or value == '':
        return value
    k = kind(field)
    if k in {'array', 'text_array'} and isinstance(value, str):
        value = [part.strip() for part in re.split(r'[;\n]', value) if part.strip()]
    elif k == 'string' and isinstance(value, list) and all(isinstance(v, str) for v in value):
        value = '; '.join(v.strip() for v in value if v.strip())
    elif k in {'integer', 'number'} and isinstance(value, str):
        text = value.strip().replace(',', '')
        if re.fullmatch(r'-?\d+', text):
            value = int(text)
        elif k == 'number' and re.fullmatch(r'-?\d*\.\d+', text):
            value = float(text)
    elif k == 'integer' and type(value) is float and value.is_integer():
        value = int(value)
    elif k == 'boolean' and isinstance(value, str) and value.strip().lower() in {'true', 'false'}:
        value = value.strip().lower() == 'true'
    options = field.get('enum', field.get('options'))
    if isinstance(options, list):
        declared = {_norm(option): option for option in options if isinstance(option, str)}
        pick = lambda v: declared.get(_norm(v), v) if isinstance(v, str) else v
        value = [pick(v) for v in value] if isinstance(value, list) else pick(value)
    return value
