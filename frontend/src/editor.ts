import type {Graph, Link} from "./types";
export type History = {past: Graph[]; present: Graph; future: Graph[]};
export function historyReducer(state: History, action: {type: "edit" | "reset"; graph: Graph} | {type: "undo" | "redo"}): History {
  if (action.type === "reset") return {past: [], present: action.graph, future: []};
  if (action.type === "edit") return {past: [...state.past.slice(-49), state.present], present: action.graph, future: []};
  if (action.type === "undo" && state.past.length) return {past: state.past.slice(0,-1), present: state.past[state.past.length-1], future: [state.present, ...state.future]};
  if (action.type === "redo" && state.future.length) return {past: [...state.past, state.present], present: state.future[0], future: state.future.slice(1)};
  return state;
}
// Reused multi-port workbench checks, generalized to optional contracts.
export function connectionError(graph: Graph, edge: Link, dag: boolean): string | null {
  const source = graph.nodes.find(n => n.id === edge.source)?.ports.find(p => p.id === edge.source_port);
  const target = graph.nodes.find(n => n.id === edge.target)?.ports.find(p => p.id === edge.target_port);
  if (!source || !target) return "节点或端口不存在";
  if (source.io !== "output" || target.io !== "input") return "请从输出连接输入";
  if (graph.edges.some(e => e.source === edge.source && e.source_port === edge.source_port && e.target === edge.target && e.target_port === edge.target_port && e.kind === edge.kind)) return "连接已存在";
  if (target.multiplicity === "one" && graph.edges.some(e => e.target === edge.target && e.target_port === edge.target_port)) return "单输入端口已有来源，请重连";
  if (edge.kind === "data") {
    if (source.data_type && target.data_type && source.data_type !== target.data_type) return "类型不一致";
    if (source.schema_ref && target.schema_ref && source.schema_ref !== target.schema_ref) return "Schema 不一致";
    if (Object.entries(target.semantics || {}).some(([k,v]) => source.semantics?.[k] !== v)) return "语义约束不一致";
  }
  if (dag) {
    const stack = [edge.target], seen = new Set<string>();
    while (stack.length) {const id = stack.pop()!; if (id === edge.source) return "连接会形成环路"; if (seen.has(id)) continue; seen.add(id); graph.edges.filter(e => e.source === id && ["data","control"].includes(e.kind)).forEach(e => stack.push(e.target));}
  }
  return null; // Unknown type is displayed as unverified, never claimed compatible.
}
