class Detection:
    def __init__(self, name: str, version: str, confidence: int, source: list[str], requirements: set[str | int] | None = None):
        self.name = name
        self.version = version.strip() or None
        self.confidence = confidence
        self.source = source
        self.requirements = requirements

    def __str__(self):
        version = f' version:{self.version}' if self.version else ''
        return f'{self.name}{version} - {self.confidence}'

    def __iadd__(self, other: Detection):
        self.confidence = min(100, self.confidence + other.confidence)
        if self.version is None:
            self.version = other.version
        self.source.extend(other.source)

        return self
