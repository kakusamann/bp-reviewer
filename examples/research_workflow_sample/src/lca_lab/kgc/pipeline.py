"""Synthetic KGC orchestration. Not called by the acceptance test."""

from .registry import select_model
from .dataset import load_triples, normalize_triples


def prepare_data():
    triples = load_triples()
    return normalize_triples(triples)


def train_model(model_name: str = "RotatE", embedding_dim: int = 200,
                learning_rate: float = 0.001, epochs: int = 15):
    config_source = "config/experiment.json"
    data = prepare_data()
    model = select_model(model_name)
    return {"model": model, "data": data, "config_path": config_source,
            "embedding_dim": embedding_dim, "learning_rate": learning_rate,
            "epochs": epochs}


def run_kgc():
    return train_model()
