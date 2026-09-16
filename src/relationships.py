"""Per-visit relationship scheduling; excludes is deliberately not implemented.

Named requirements use the crawler's existing AND policy; categories use OR.
Direct evidence adds as before. Implied confidence uses the strongest path,
not a sum, so cycles and overlapping implication chains cannot inflate it.
"""

from collections import defaultdict, deque
from copy import deepcopy
from typing import Any

from Detection import Detection


Definition = dict[str, Any]
QueueItem = tuple[Definition, Detection]


def as_list(value):
    return value if isinstance(value, (list, tuple, set)) else [value]


class Relationships:
    def __init__(self, queue: deque[QueueItem], detections: dict[str, Detection]):
        self.queue = queue
        self.detections = detections
        self.direct: dict[str, Detection] = {}
        self.categories: set[int] = set()
        self.categories_by_name: dict[str, set[int]] = defaultdict(set)
        self.implies: dict[str, list[tuple[str, int, str]]] = defaultdict(list)
        self.pending: dict[int, QueueItem] = {}
        self.awaiting_names: dict[str, set[int]] = defaultdict(set)
        self.awaiting_categories: dict[int, set[int]] = defaultdict(set)
        self.order = {id(detection): index for index, (_, detection) in enumerate(queue)}

        for definition, _ in queue:
            name = definition['name']
            self.categories_by_name[name].update(as_list(definition.get('cats', [])))
            for reference in as_list(definition.get('implies', [])):
                target, *instructions = reference.split(r'\;')
                tags = dict(instruction.split(':', 1) for instruction in instructions)
                confidence = int(tags.get('confidence', 100))
                if not 0 <= confidence <= 100:
                    raise ValueError(f'Invalid implication confidence: {reference}')
                relation = (target, confidence, tags.get('version', ''))
                if relation not in self.implies[name]:
                    self.implies[name].append(relation)

    def _ready(self, definition: Definition, detection: Detection) -> bool:
        if detection.requirements is None:
            detection.requirements = set(as_list(definition.get('requires', [])))
        detection.requirements.difference_update(self.detections)
        categories = set(as_list(definition.get('requiresCategory', [])))
        return not detection.requirements and (
            not categories or bool(categories & self.categories)
        )

    def defer(self, definition: Definition, detection: Detection) -> bool:
        """Park an ineligible definition; only ready definitions are requeued."""
        if self._ready(definition, detection):
            return False
        key = id(detection)
        self.pending[key] = (definition, detection)
        for name in detection.requirements:
            self.awaiting_names[name].add(key)
        for category in as_list(definition.get('requiresCategory', [])):
            if category not in self.categories:
                self.awaiting_categories[category].add(key)
        return True

    def _wake(self, name: str):
        waiting = self.awaiting_names.pop(name, set())
        new_categories = self.categories_by_name[name] - self.categories
        self.categories.update(new_categories)
        for category in new_categories:
            waiting.update(self.awaiting_categories.pop(category, set()))
        for key in sorted(waiting, key=self.order.__getitem__):
            item = self.pending.get(key)
            if item is not None and self._ready(*item):
                self.queue.append(self.pending.pop(key))

    def add_detection(self, detection: Detection):
        """Aggregate direct evidence, expand implications, and unlock dependants."""
        name = detection.name
        if name in self.direct:
            self.direct[name] += deepcopy(detection)
        else:
            self.direct[name] = deepcopy(detection)

        direct = self.direct[name]
        if name not in self.detections:
            self.detections[name] = deepcopy(direct)
        else:
            result = self.detections[name]
            result.confidence = max(result.confidence, direct.confidence)
            if result.version is None:
                result.version = direct.version
            for source in direct.source:
                if source not in result.source:
                    result.source.append(source)

        changed = deque([name])
        while changed:
            source_name = changed.popleft()
            self._wake(source_name)
            source = self.detections[source_name]
            for target, limit, version in self.implies[source_name]:
                confidence = min(source.confidence, limit)
                if target not in self.detections:
                    self.detections[target] = Detection(
                        target, version, confidence, ['implies']
                    )
                    changed.append(target)
                    continue

                result = self.detections[target]
                if confidence > result.confidence:
                    result.confidence = confidence
                    changed.append(target)
                # Constant relation versions need no regex captures.
                result += Detection(target, version, 0, [])
                if 'implies' not in result.source:
                    result.source.append('implies')
