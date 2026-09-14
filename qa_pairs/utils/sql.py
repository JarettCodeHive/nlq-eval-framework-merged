"""Safe SQL string-literal quoting and the Jinja environment factory.

Templates interpolate values with the `sqlstr` filter, never bare:
    WHERE a.region = {{ region | sqlstr }}
so a value containing an apostrophe (O'Brien Ltd) or any quote is
escaped, not injected.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined


def sqlstr(value) -> str:
    """Render `value` as a single-quoted SQL string literal (quotes doubled)."""
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def jinja_env(template_dir: str | Path) -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        undefined=StrictUndefined,  # a missing template var is a hard error
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["sqlstr"] = sqlstr
    return env
