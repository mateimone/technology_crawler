import json
from pathlib import Path

from Detection import Detection


def write_results(
    results: dict,
    website: str,
    detections: dict[str, Detection],
    file: Path,
    *,
    failed: bool = False,
):
    technologies = results.setdefault(website, {})
    for name, detection in detections.items():
        technology = technologies.setdefault(name, {'version': None, 'confidence': 0})
        if technology['version'] is None:
            technology['version'] = detection.version
        technology['confidence'] = max(technology['confidence'], min(detection.confidence, 100))

    if failed:
        technologies['failed'] = ''
    else:
        technologies.pop('failed', None)

    file.write_text(json.dumps(results, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
