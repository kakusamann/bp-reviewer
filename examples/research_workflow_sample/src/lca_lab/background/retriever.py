"""Separate background activity matching functionality."""


def load_background_catalog():
    return read_csv("data/background_catalog.csv")


def match_exchange(query: str, top_k: int = 5):
    candidates = load_background_catalog()
    return candidates[:top_k]
