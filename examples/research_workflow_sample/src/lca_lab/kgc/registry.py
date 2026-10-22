"""Synthetic registered KGC baselines for blueprint testing only."""

class TransE:
    def score(self, head, relation, tail):
        return head + relation - tail


class RotatE:
    def score(self, head, relation, tail):
        return (head + relation) * tail


class RGCN:
    def predict(self, triples):
        return list(triples)


MODEL_REGISTRY = {
    "TransE": TransE,
    "RotatE": RotatE,
    "RGCN": RGCN,
}


def select_model(name: str = "RotatE"):
    return MODEL_REGISTRY[name]()
