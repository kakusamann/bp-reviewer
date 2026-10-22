"""Synthetic triple-data contract."""

from dataclasses import dataclass


@dataclass
class TripleRecord:
    head: str
    relation: str
    tail: str


def load_triples(path: str = "data/triples.csv"):
    return read_csv("data/triples.csv")


def normalize_triples(triples):
    return [tuple(value.strip() for value in triple) for triple in triples]
