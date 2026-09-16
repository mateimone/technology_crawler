import json
import regex as re
from pathlib import Path
from typing import Any

DATABASES = Path(__file__).resolve().parent.parent / "databases"

def _index(records):
    return {record['name']: record for record in records}

def load_webappanalyzer(directory: str | Path = DATABASES / "webappanalyzer"):
    records = []
    technologies_dir = Path(directory).resolve() / "src" / "technologies"
    if not technologies_dir.is_dir():
        raise FileNotFoundError(f"Fingerprint directory not found: {technologies_dir}")
    for path in sorted(technologies_dir.glob("*.json")):
        definitions = json.loads(path.read_text(encoding="utf-8-sig"))
        for name, definition in definitions.items():
            records.append({**definition, "name": name, "source_file": str(path)})
    if not records:
        raise ValueError(f"No technology definitions found in: {technologies_dir}")
    return _index(records)


def compile_pattern(raw_pattern):
    pattern, *instructions = raw_pattern.split(r"\;")

    tags = {}

    for instruction in instructions:
        if ":" not in instruction:
            continue

        name, value = instruction.split(":", 1)
        tags[name] = value

    compiled = re.compile(pattern, re.IGNORECASE)

    return {
        "compiled": compiled,
        "confidence": int(tags.get("confidence", 100)),
        "version": tags.get("version"),
    }


def compile_definition_patterns(definition, compiled_patterns: dict[str, dict[str, Any]]):
    def compile_values(value):
        if isinstance(value, str):
            if value not in compiled_patterns:
                compiled_patterns[value] = compile_pattern(value)
        elif isinstance(value, dict):
            for item in value.values():
                compile_values(item)
        elif isinstance(value, list):
            for item in value:
                compile_values(item)

    for source in ('html', 'text', 'css', 'url', 'xhr', 'scriptSrc', 'scripts',
                   'js', 'meta', 'headers', 'cookies', 'dns'):
        compile_values(definition.get(source, []))

    dom = definition.get('dom', {})
    if isinstance(dom, dict):
        for checks in dom.values():
            compile_values(checks or {'exists': ''})
    elif dom:
        compile_values('')
