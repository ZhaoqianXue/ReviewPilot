"""One value contract for model extraction and human corrections.

None and the empty string mean not reported, including for required fields.
"""
import math
import json


def kind(field):
    raw = str(field.get('type') or 'Text').strip().lower()
    aliases = {'text':'string','long text':'string','str':'string','categorical':'string','select':'string',
               'int':'integer','float':'number','double':'number','list':'array','text (list)':'text_array',
               'list[str]':'text_array','list[string]':'text_array','dict':'object','bool':'boolean'}
    return aliases.get(raw, raw)


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
