export type Evidence = {
  path: string;
  line: number;
  sha256: string;
  status: string;
  note: string;
};
export type Port = {
  id: string;
  label?: string;
  io: "input" | "output";
  data_type?: string | null;
  schema_ref?: string | null;
  multiplicity?: "one" | "many";
  required?: boolean;
  semantics?: Record<string, string>;
  description?: string;
};
export type ProjectNode = {
  id: string;
  label: string;
  kind: string;
  parent: string;
  ports: Port[];
  position: { x: number; y: number };
  parameters: Record<string, unknown>;
  evidence: Evidence[];
  description: string;
};
export type Link = {
  id: string;
  source: string;
  source_port: string;
  target: string;
  target_port: string;
  kind: "data" | "control" | "import" | "contains";
  status: string;
  evidence: Evidence[];
};
export type Graph = { nodes: ProjectNode[]; edges: Link[] };
export type Snapshot = {
  id: string;
  project_name: string;
  git_head: string | null;
  graph: Graph;
  warnings: string[];
  require_dag: boolean;
};
