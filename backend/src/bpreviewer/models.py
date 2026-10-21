"""Portable graph and manifest contracts, independent of inspected projects."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Status = Literal["STATIC_INFERRED", "UNVERIFIED", "DECLARED", "RUN_OBSERVED"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(Model):
    path: str
    line: int = Field(default=1, ge=1)
    sha256: str = ""
    status: Status = "STATIC_INFERRED"
    note: str = ""


class Port(Model):
    id: str
    label: str = ""
    io: Literal["input", "output"]
    data_type: str | None = None
    schema_ref: str | None = None
    required: bool = False
    multiplicity: Literal["one", "many"] = "many"
    semantics: dict[str, str] = Field(default_factory=dict)
    description: str = "未知类型需人工确认；静态关系不代表实际执行。"


class Node(Model):
    id: str
    label: str
    kind: str = "module"
    parent: str = ""
    ports: list[Port] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    position: dict[str, float] = Field(default_factory=lambda: {"x": 0, "y": 0})
    evidence: list[Evidence] = Field(default_factory=list)
    description: str = ""

    @model_validator(mode="after")
    def unique_ports(self):
        if len({p.id for p in self.ports}) != len(self.ports):
            raise ValueError("Duplicate port IDs")
        return self


class Edge(Model):
    id: str
    source: str
    source_port: str
    target: str
    target_port: str
    kind: Literal["data", "control", "import", "contains"] = "data"
    status: Status = "UNVERIFIED"
    evidence: list[Evidence] = Field(default_factory=list)


class Graph(Model):
    nodes: list[Node] = Field(default_factory=list, max_length=6000)
    edges: list[Edge] = Field(default_factory=list, max_length=20000)

    @model_validator(mode="after")
    def unique_ids(self):
        for items in (self.nodes, self.edges):
            if len({n.id for n in items}) != len(items):
                raise ValueError("Duplicate graph IDs")
        return self


class Manifest(Model):
    version: Literal["1"] = "1"
    graph: Graph
    require_dag: bool = True
    description: str = ""


class Snapshot(Model):
    id: str
    project_name: str
    git_head: str | None = None
    graph: Graph
    warnings: list[str] = Field(default_factory=list)
    require_dag: bool = False
