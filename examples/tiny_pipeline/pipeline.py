"""Synthetic project: numeric pipeline, no data files or external services."""
from transform import normalize


def load(value: float) -> float:
    return value


def summarize(A: float, B: float = 0) -> float:
    return A + B


def main(value: float) -> float:
    raw = load(value)
    clean = normalize(raw)
    return summarize(clean)
