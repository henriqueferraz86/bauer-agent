"""Utilitários para Unicode seguro no Windows (surrogates, cp1252).

No Windows, os.fsdecode() usa 'surrogateescape' para nomes de arquivo com
bytes inválidos, introduzindo U+D800–U+DFFF (lone surrogates) em strings
Python. Isso causa:
  - 'surrogates not allowed' no json.dumps(ensure_ascii=False)
  - 'surrogates not allowed' no .encode('utf-8')

As funções deste módulo sanitizam strings recursivamente antes da serialização.
"""

from __future__ import annotations

import json
from typing import Any


def sanitize_surrogates(obj: Any) -> Any:
    r"""Remove lone surrogates de strings, dicts e lists recursivamente.

    Usa encode/decode com errors='replace': no ENCODE, cada surrogate vira
    '?' (o replacement de encode é ASCII '?', não U+FFFD). Tipos não-string
    são retornados sem modificação.

    Docstring é raw: com escape processado, "\udcff" viraria um surrogate
    REAL dentro do __doc__ compilado — o Python 3.14 rejeita isso na
    importação do módulo (UnicodeEncodeError: surrogates not allowed).

    Exemplo::

        >>> sanitize_surrogates("abc\udcffdef")
        'abc?def'
        >>> sanitize_surrogates({"path": "C:\\bad\udcffname"})
        {'path': 'C:\\bad?name'}
    """
    if isinstance(obj, str):
        # round-trip: encode remove surrogates, decode restitui string limpa
        return obj.encode("utf-8", errors="replace").decode("utf-8")
    if isinstance(obj, dict):
        return {
            sanitize_surrogates(k): sanitize_surrogates(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [sanitize_surrogates(item) for item in obj]
    if isinstance(obj, tuple):
        return tuple(sanitize_surrogates(item) for item in obj)
    return obj


def safe_json_dumps(obj: Any, **kwargs: Any) -> str:
    """json.dumps com sanitização preventiva de surrogates.

    json.dumps(ensure_ascii=False) em CPython pode:
      - Levantar UnicodeEncodeError imediatamente (Linux/Mac), OU
      - Retornar uma str Python contendo surrogates (Windows), que explode
        apenas ao chamar file.write() com encoding='utf-8'.

    Por isso sanitizamos o objeto ANTES de passar ao json.dumps — não como
    fallback, mas sempre. Strings limpas não são afetadas (round-trip seguro).

    Uso::

        safe_json_dumps(data, ensure_ascii=False, indent=2)
    """
    # Sanitiza preventivamente — não aguarda exceção (ver docstring)
    clean = sanitize_surrogates(obj)
    try:
        return json.dumps(clean, **kwargs)
    except (UnicodeEncodeError, UnicodeDecodeError):
        # Fallback extremo: force ASCII escape
        kwargs.pop("ensure_ascii", None)
        return json.dumps(clean, ensure_ascii=True, **kwargs)
