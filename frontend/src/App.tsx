import { useEffect, useMemo, useReducer, useState } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  Handle,
  Position,
  ReactFlowProvider,
  MarkerType,
  useReactFlow,
  applyNodeChanges,
  type NodeChange,
  type Node,
  type NodeProps,
  type Connection,
} from "@xyflow/react";
import dagre from "@dagrejs/dagre";
import { api } from "./api";
import { connectionError, historyReducer } from "./editor";
import type {
  Evidence,
  Graph,
  Link,
  Port,
  ProjectNode,
  Snapshot,
  DirectoryEntry,
} from "./types";
import "@xyflow/react/dist/style.css";

type CardData = {
  item: ProjectNode;
  editable: boolean;
  choose: (id: string, p: Port) => void;
  enter: (id: string) => void;
};
function Card({ data }: NodeProps<Node<CardData>>) {
  return (
    <article className="card" data-kind={data.item.kind}
      onDoubleClick={(event) => {
        if (data.item.kind === "logical_module") {
          event.stopPropagation();
          data.enter(data.item.id);
        }
      }}>
      <small>
        {data.item.kind} · {data.editable ? "UNVERIFIED · 拟议" : data.item.evidence[0]?.status || "UNVERIFIED"}
      </small>
      <strong>{data.item.label}</strong>
      {data.item.ports.map((p) => (
        <div className={`port ${p.io}`} key={p.id}>
          <Handle
            id={p.id}
            type={p.io === "input" ? "target" : "source"}
            position={p.io === "input" ? Position.Left : Position.Right}
            isConnectable={data.editable}
            title={p.description || "结构连接端口；仅静态证据支持的读写关系显示具体数据类型"}
          />
          <button
            className="nodrag"
            onClick={() => data.choose(data.item.id, p)}
            title={p.description || "输入输出端口；数据对象以源码静态证据为准"}
          >
            {p.label || p.id}
            {p.data_type && <em>{p.data_type}</em>}
          </button>
        </div>
      ))}
    </article>
  );
}
const nodeTypes = { project: Card };
const empty: Graph = { nodes: [], edges: [] };
export function App() {
  return (
    <ReactFlowProvider>
      <Workbench />
    </ReactFlowProvider>
  );
}
function Workbench() {
  const [token, setToken] = useState(
    sessionStorage.getItem("bpreviewer.token") || "",
  );
  const [darkMode, setDarkMode] = useState(() => localStorage.getItem("bpreviewer.theme") === "dark");
  useEffect(() => {
    document.documentElement.dataset.theme = darkMode ? "dark" : "light";
    localStorage.setItem("bpreviewer.theme", darkMode ? "dark" : "light");
  }, [darkMode]);
  const [path, setPath] = useState("");
  const [roots, setRoots] = useState<string[]>([]);
  const [treeEntries, setTreeEntries] = useState<DirectoryEntry[]>([]);
  const [treeQuery, setTreeQuery] = useState("");
  const [expandedDirs, setExpandedDirs] = useState<string[]>([]);
  const [selectedPath, setSelectedPath] = useState("");
  const [treeTruncated, setTreeTruncated] = useState(false);
  const [semanticGraph, setSemanticGraph] = useState<Graph>();
  const [viewMode, setViewMode] = useState<"static" | "logical">("static");
  const [analysisMeta, setAnalysisMeta] = useState<{
    model: string;
    analyzed_files: number;
    logical_modules: number;
    project_domains?: number;
    data_artifacts: number;
    unreviewed_files: number;
    cache_hit: boolean;
    warnings: string[];
    recursive_domains_built?: number;
    recursive_domains_total?: number;
    local_rebuild?: boolean;
    scope_label?: string;
    scope_files?: number;
  }>();
  const [snapshot, setSnapshot] = useState<Snapshot>();
  const [mode, setMode] = useState<"As-is" | "Proposed" | "Diff">("As-is");
  const [history, dispatch] = useReducer(historyReducer, {
    past: [],
    present: empty,
    future: [],
  });
  const [scope, setScope] = useState("modules");
  const [showDependencies, setShowDependencies] = useState(false);
  const [detailLayer, setDetailLayer] = useState<"logic" | "evidence" | "mixed">("logic");
  const [layoutOverrides, setLayoutOverrides] = useState<Record<string, { x: number; y: number }>>({});
  const [buildProgress, setBuildProgress] = useState<{
    status: string; stage: string; detail: string; completed_steps: number; total_steps: number; error?: string;
  }>();
  const [showBuildProgress, setShowBuildProgress] = useState(false);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState("");
  const [edgeId, setEdgeId] = useState("");
  const [pending, setPending] = useState<{ id: string; port: string }>();
  const [edgeKind, setEdgeKind] = useState<"data" | "control">("data");
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState<{
    json: { operations: unknown[] };
    markdown: string;
  }>();
  const [source, setSource] = useState("");
  const [portsText, setPortsText] = useState("");
  const [paramsText, setParamsText] = useState("");
  const flow = useReactFlow();
  const editable = mode === "Proposed";
  const graph =
    mode === "As-is"
      ? (viewMode === "logical" && semanticGraph ? semanticGraph : snapshot?.graph || empty)
      : history.present;
  const current = graph.nodes.find((n) => n.id === selected);
  const edit = (next: Graph) => {
    dispatch({ type: "edit", graph: next });
    setReport(undefined);
  };
  const run = async (work: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try {
      await work();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };
  const connect = (c: Connection, oldId?: string) => {
    if (!editable) return;
    const base = {
      ...history.present,
      edges: history.present.edges.filter((e) => e.id !== oldId),
    };
    const edge: Link = {
      id: crypto.randomUUID(),
      source: c.source,
      target: c.target,
      source_port: c.sourceHandle || "",
      target_port: c.targetHandle || "",
      kind: edgeKind,
      status: "UNVERIFIED",
      evidence: [],
    };
    const message = connectionError(base, edge, snapshot?.require_dag || false);
    if (message) {
      setError(message);
      return;
    }
    edit({ ...base, edges: [...base.edges, edge] });
    setPending(undefined);
    setInfo("已加入拟议连接；不修改目标源码，未知类型仍待确认。");
  };
  const portClick = (id: string, port: Port) => {
    setSelected(id);
    setInfo(
      `${port.label || port.id}: ${port.description || "输入输出端口"} · ${port.schema_ref || "合同未声明"}`,
    );
    if (!editable) return;
    if (port.io === "output") {
      setPending({ id, port: port.id });
      setInfo("已选输出；点击输入端口连接。");
    } else if (pending)
      connect({
        source: pending.id,
        sourceHandle: pending.port,
        target: id,
        targetHandle: port.id,
      });
  };
  const visible = useMemo(() => {
    const isLogical = viewMode === "logical" && mode === "As-is";
    const primary = graph.nodes.filter((node) => {
      const belongs = scope === "all" ||
        (scope === "modules" ? !node.parent : node.parent === scope);
      if (!belongs || (query && !node.label.toLowerCase().includes(query.toLowerCase()))) return false;
      if (!isLogical || detailLayer === "mixed") return true;
      if (detailLayer === "logic") {
        return node.kind === "logical_module" && node.label !== "工程与辅助资源";
      }
      return node.kind !== "logical_module";
    });
    const visibleIds = new Set(primary.map((node) => node.id));
    if (detailLayer === "mixed" && scope !== "modules" && scope !== "all") {
      const byId = new Map(graph.nodes.map((node) => [node.id, node]));
      for (const edge of graph.edges) {
        const sourceInside = visibleIds.has(edge.source);
        const targetInside = visibleIds.has(edge.target);
        if (sourceInside === targetInside) continue;
        const id = sourceInside ? edge.target : edge.source;
        const external = byId.get(id);
        if (external && ["logical_module", "data_artifact"].includes(external.kind)) visibleIds.add(id);
      }
    }
    return graph.nodes.filter((node) => visibleIds.has(node.id));
  }, [graph, scope, query, viewMode, mode, detailLayer]);
  const visibleTreeEntries = useMemo(() => {
    if (!treeQuery.trim()) {
      return treeEntries.filter((entry) => !entry.parent || expandedDirs.includes(entry.parent));
    }
    const queryText = treeQuery.trim().toLowerCase();
    const byPath = new Map(treeEntries.map((entry) => [entry.path, entry]));
    const allowed = new Set<string>();
    for (const entry of treeEntries) {
      if ((entry.name + " " + entry.path).toLowerCase().includes(queryText)) {
        let current: DirectoryEntry | undefined = entry;
        while (current) {
          allowed.add(current.path);
          current = current.parent ? byPath.get(current.parent) : undefined;
        }
        if (entry.kind === "directory") {
          for (const child of treeEntries) {
            if (child.path.startsWith(entry.path + "/")) allowed.add(child.path);
          }
        }
      }
    }
    return treeEntries.filter((entry) => allowed.has(entry.path));
  }, [treeEntries, treeQuery, expandedDirs]);
  const visibleNodeIds = new Set(visible.map((node) => node.id));
  const diagramLinks = graph.edges.filter(
    (edge) =>
      visibleNodeIds.has(edge.source) &&
      visibleNodeIds.has(edge.target) &&
      (viewMode !== "logical" || showDependencies || ["data", "control"].includes(edge.kind)),
  );
  const renderedNodes: Node[] = visible.map((node, i) => ({
    id: node.id,
    type: "project",
    position: node.position.x || node.position.y
      ? node.position
      : { x: (i % 3) * 360, y: Math.floor(i / 3) * 290 },
    selected: node.id === selected,
    data: { item: node, editable, choose: portClick,
      enter: (id: string) => {
        setScope(id);
        setQuery("");
        setSelected("");
        setEdgeId("");
      },
    },
  }));
  if (viewMode === "logical" && mode === "As-is" && visible.length <= 200) {
    if (scope === "modules" || diagramLinks.length === 0) {
      // Root overviews should remain legible even when no data-flow edge exists.
      const columns = 3;
      let top = 40;
      for (let row = 0; row < visible.length; row += columns) {
        const entries = visible.slice(row, row + columns);
        const rowHeight = Math.max(...entries.map((entry) => Math.max(115, 80 + entry.ports.length * 42)));
        entries.forEach((_, offset) => {
          renderedNodes[row + offset].position = { x: 45 + offset * 380, y: top };
        });
        top += rowHeight + 75;
      }
    } else {
      const layoutGraph = new dagre.graphlib.Graph({ multigraph: false });
      layoutGraph.setGraph({ rankdir: "LR", ranksep: 140, nodesep: 90, marginx: 50, marginy: 45 });
      layoutGraph.setDefaultEdgeLabel(() => ({}));
      visible.forEach((node) => layoutGraph.setNode(node.id, {
        width: 290,
        height: Math.max(115, 80 + node.ports.length * 42),
      }));
      diagramLinks.forEach((edge) => {
        if (edge.source !== edge.target) layoutGraph.setEdge(edge.source, edge.target);
      });
      dagre.layout(layoutGraph);
      renderedNodes.forEach((node) => {
        const entry = layoutGraph.node(node.id);
        if (entry) node.position = { x: entry.x - entry.width / 2, y: entry.y - entry.height / 2 };
      });
    }
  }
  // Visual layout is user-adjustable even when immutable graph facts are read-only.
  renderedNodes.forEach((node) => {
    if (layoutOverrides[node.id] && mode === "As-is") node.position = layoutOverrides[node.id];
  });
  const [canvasNodes, setCanvasNodes] = useState<Node[]>([]);
  useEffect(() => {
    setCanvasNodes(renderedNodes);
  }, [graph, scope, query, selected, editable, pending, detailLayer, layoutOverrides]);
  useEffect(() => {
    if (!snapshot) return;
    try {
      const stored = localStorage.getItem(`bpreviewer.layout.${snapshot.id}`);
      setLayoutOverrides(stored ? JSON.parse(stored) : {});
    } catch {
      setLayoutOverrides({});
    }
  }, [snapshot?.id]);
  const nodes = canvasNodes;
  useEffect(() => {
    if (viewMode !== "logical" || mode !== "As-is") return;
    const handle = setTimeout(() => void flow.fitView({ padding: 0.12, duration: 250 }), 80);
    return () => clearTimeout(handle);
  }, [scope, viewMode, showDependencies, semanticGraph, mode, flow]);
  const moveNodes = (changes: NodeChange[]) =>
    setCanvasNodes((previous) => applyNodeChanges(changes, previous));
  const ids = new Set(visible.map((n) => n.id));
  const edges = diagramLinks
    .filter((e) => ids.has(e.source) && ids.has(e.target))
    .map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      sourceHandle: e.source_port,
      targetHandle: e.target_port,
      label: `${e.kind} · ${editable ? "拟议" : e.status}`,
      selected: e.id === edgeId,
      reconnectable: editable,
      markerEnd: { type: MarkerType.ArrowClosed },
      style: {
        stroke: e.kind === "data" ? (darkMode ? "#4ad4c0" : "#168f87") : e.kind === "control" ? (darkMode ? "#b4a1ff" : "#7b72cc") : "#74849a",
        strokeWidth: 2,
        strokeDasharray: e.kind === "control" ? "6 4" : undefined,
      },
    }));
  useEffect(() => {
    setPortsText(current ? JSON.stringify(current.ports, null, 2) : "");
    setParamsText(current ? JSON.stringify(current.parameters, null, 2) : "");
  }, [selected, mode, snapshot?.id]);
  useEffect(() => {
    if (snapshot && editable)
      localStorage.setItem(
        `bpreviewer.recovery.${snapshot.id}`,
        JSON.stringify(history.present),
      );
  }, [snapshot?.id, history.present, editable]);
  useEffect(() => {
    if (!editable) return;
    const prevent = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", prevent);
    return () => window.removeEventListener("beforeunload", prevent);
  }, [editable]);
  const load = () =>
    run(async () => {
      const data = await api<Snapshot>("/api/projects/scan", {
        method: "POST",
        body: JSON.stringify({ path }),
      });
      const tree = await api<{ entries: DirectoryEntry[]; truncated: boolean }>(
        "/api/snapshots/" + data.id + "/tree",
      );
      const saved = await api<{ graph: Graph; revision: number }>(
        `/api/snapshots/${data.id}/draft`,
      );
      setSnapshot(data);
      setTreeEntries(tree.entries);
      setTreeTruncated(tree.truncated);
      setExpandedDirs([]);
      setTreeQuery("");
      setSelectedPath("");
      setSource("");
      setSemanticGraph(undefined);
      setAnalysisMeta(undefined);
      setViewMode("static");
      dispatch({ type: "reset", graph: saved.graph });
      setRevision(saved.revision);
      setMode("As-is");
      setScope("modules");
      setSelected("");
      setReport(undefined);
      setPending(undefined);
      setInfo("扫描完成。As-is 为只读基线；展开模块或切换 Proposed 编辑。");
    });
  const review = () =>
    run(async () => {
      if (!snapshot) return;
      const result = await api<{
        json: { operations: unknown[] };
        markdown: string;
      }>(`/api/snapshots/${snapshot.id}/change-request`, {
        method: "POST",
        body: JSON.stringify({ graph: history.present }),
      });
      setReport(result);
      setMode("Diff");
    });
  const rebuildScope = (scopeNodeId: string, navigateAfter = true) =>
    run(async () => {
      if (!snapshot || !semanticGraph) return;
      const result = await api<{
        graph: Graph;
        metadata: NonNullable<typeof analysisMeta>;
      }>(`/api/snapshots/${snapshot.id}/semantic-blueprint/rebuild`, {
        method: "POST",
        body: JSON.stringify({ scope_node_id: scopeNodeId }),
      });
      setSemanticGraph(result.graph);
      setAnalysisMeta(result.metadata);
      setMode("As-is");
      setViewMode("logical");
      if (navigateAfter) setScope(scopeNodeId);
      setSelected("");
      setEdgeId("");
      setQuery("");
      const name = result.metadata.scope_label || "当前模块";
      setInfo(`局部重建完成：${name}，范围内 ${result.metadata.scope_files ?? 0} 个 Python 文件，深度分析 ${result.metadata.analyzed_files} 个文件。上级与其他功能域保持不变。`);
    });

  const buildBlueprint = () =>
    run(async () => {
      if (!snapshot) return;
      const target = scope !== "modules" && scope !== "all"
        ? semanticGraph?.nodes.find((node) => node.id === scope && node.kind === "logical_module")
        : undefined;
      if (target && semanticGraph) {
        const result = await api<{
          graph: Graph;
          metadata: NonNullable<typeof analysisMeta>;
        }>(`/api/snapshots/${snapshot.id}/semantic-blueprint/rebuild`, {
          method: "POST",
          body: JSON.stringify({ scope_node_id: target.id }),
        });
        setSemanticGraph(result.graph);
        setAnalysisMeta(result.metadata);
        setMode("As-is");
        setViewMode("logical");
        setSelected("");
        setEdgeId("");
        setQuery("");
        setInfo(`局部重建完成：${result.metadata.scope_label || target.label}，范围内 ${result.metadata.scope_files ?? 0} 个 Python 文件，深度分析 ${result.metadata.analyzed_files} 个文件。上级与其他功能域保持不变。`);
        return;
      }
      const result = await api<{
        graph: Graph;
        metadata: NonNullable<typeof analysisMeta>;
      }>(
        `/api/snapshots/${snapshot.id}/semantic-blueprint?force_refresh=true`,
        { method: "POST" },
      );
      setSemanticGraph(result.graph);
      setAnalysisMeta(result.metadata);
      setMode("As-is");
      setViewMode("logical");
      setScope("modules");
      setSelected("");
      setEdgeId("");
      setQuery("");
      const recursion = result.metadata.recursive_domains_built === undefined
        ? ""
        : `；已递归整理 ${result.metadata.recursive_domains_built}/${result.metadata.recursive_domains_total ?? result.metadata.recursive_domains_built} 个功能域`;
      const cacheNote = result.metadata.cache_hit ? "（命中缓存）" : "";
      setInfo(`全局逻辑蓝图已构建：${result.metadata.analyzed_files} 个深度分析文件、${result.metadata.project_domains ?? "若干"} 个功能域、${result.metadata.data_artifacts} 个数据资产节点${recursion}${cacheNote}。`);
    });

  const download = (format: "json" | "markdown") => {
    if (!report) return;
    const blob = new Blob(
      [
        format === "json"
          ? JSON.stringify(report.json, null, 2)
          : report.markdown,
      ],
      { type: format === "json" ? "application/json" : "text/markdown" },
    );
    const url = URL.createObjectURL(blob),
      a = document.createElement("a");
    a.href = url;
    a.download = `bp-reviewer-change-request.${format === "json" ? "json" : "md"}`;
    a.click();
    URL.revokeObjectURL(url);
  };
  const revealTreePath = (filePath: string) => {
    setSelectedPath(filePath);
    const parts = filePath.split("/");
    const ancestors = parts.slice(0, -1).map((_, index) => parts.slice(0, index + 1).join("/"));
    setExpandedDirs((previous) => Array.from(new Set([...previous, ...ancestors])));
  };
  const openSource = (filePath: string, line = 1) =>
    run(async () => {
      revealTreePath(filePath);
      const entry = treeEntries.find((item) => item.path === filePath);
      if (entry && !entry.previewable) {
        setSource(filePath + "\n\n该文件为敏感文件、非文本文件或超过大小限制，已禁用预览。");
        return;
      }
      const data = await api<{
        content: string;
        first_line: number;
        stale: boolean;
        indexed: boolean;
      }>(
        "/api/snapshots/" + snapshot!.id + "/source?path=" + encodeURIComponent(filePath) + "&line=" + line,
      );
      const flags = [
        data.stale ? "源码已变化，需重新扫描" : "",
        data.indexed ? "" : "目录文件（尚无图谱证据）",
      ].filter(Boolean).join(" · ");
      setSource(filePath + ":" + data.first_line + (flags ? " · " + flags : "") + "\n" + data.content);
    });
  const showSource = (p: Evidence) => void openSource(p.path, p.line);
  const openTreeFile = (entry: DirectoryEntry) => {
    revealTreePath(entry.path);
    const evidenceNode = graph.nodes.find((node) => node.evidence.some((proof) => proof.path === entry.path));
    if (evidenceNode) {
      setScope(evidenceNode.parent || evidenceNode.id);
      setQuery("");
      setSelected(evidenceNode.id);
    }
    void openSource(entry.path, 1);
  };
  const layout = () => {
    const g = new dagre.graphlib.Graph();
    g.setGraph({ rankdir: "LR", nodesep: 70, ranksep: 100 });
    g.setDefaultEdgeLabel(() => ({}));
    nodes.forEach((n) =>
      g.setNode(n.id, {
        width: 285,
        height:
          100 +
          (graph.nodes.find((v) => v.id === n.id)?.ports.length || 0) * 45,
      }),
    );
    edges.forEach((e) => g.setEdge(e.source, e.target));
    dagre.layout(g);
    if (editable)
      edit({
        ...history.present,
        nodes: history.present.nodes.map((n) => {
          const p = g.node(n.id);
          return p
            ? {
                ...n,
                position: { x: p.x - p.width / 2, y: p.y - p.height / 2 },
              }
            : n;
        }),
      });
    setTimeout(() => void flow.fitView(), 30);
  };
  return (
    <div className="app">
      <header>
        <div>
          <small>PROJECT LOGIC & CHANGE REVIEW</small>
          <h1>BP-Reviewer</h1>
        </div>
        <span>只读扫描 · 独立草稿 · 人工评审</span>
        <button
          type="button"
          className="theme-toggle"
          aria-label="切换界面主题"
          aria-pressed={darkMode}
          onClick={() => setDarkMode((previous) => !previous)}
        >
          {darkMode ? "浅色模式" : "深色模式"}
        </button>
      </header>
      <section className="connection">
        <input
          aria-label="本地访问令牌"
          type="password"
          placeholder="终端中的本地访问令牌"
          value={token}
          onChange={(e) => setToken(e.target.value)}
        />
        <button
          onClick={() =>
            void run(async () => {
              sessionStorage.setItem("bpreviewer.token", token);
              const data = await api<{ allowed_roots: string[] }>(
                "/api/projects",
              );
              setRoots(data.allowed_roots);
              setPath(data.allowed_roots[0] || "");
              setInfo("已连接本地服务");
            })
          }
        >
          连接服务
        </button>
        <input
          aria-label="项目目录"
          list="roots"
          placeholder="外部 Python 项目目录"
          value={path}
          onChange={(e) => setPath(e.target.value)}
        />
        <datalist id="roots">
          {roots.map((r) => (
            <option key={r} value={r} />
          ))}
        </datalist>
        <button disabled={busy || !path} onClick={() => void load()}>
          扫描项目
        </button>
      </section>
      {error && (
        <div role="alert" className="error">
          {error}
        </div>
      )}
      {busy && <div role="status">处理中…</div>}
      <div className="notice">
        {info || "从终端启动独立后端后，输入令牌并选择项目。"}
      </div>
      {snapshot ? (
        <>
          <nav>
            {(["As-is", "Proposed", "Diff"] as const).map((m) => (
              <button
                className={mode === m ? "active" : ""}
                key={m}
                onClick={() => {
                  if (m === "Diff") void review();
                  else {
                    setMode(m);
                    if (m !== "As-is") setViewMode("static");
                  }
                }}
              >
                {m}
              </button>
            ))}
            {semanticGraph && mode === "As-is" && (
              <>
                <button
                  className={viewMode === "static" ? "active" : ""}
                  onClick={() => { setViewMode("static"); setSelected(""); setScope("modules"); }}
                >
                  静态证据
                </button>
                <button
                  className={viewMode === "logical" ? "active" : ""}
                  onClick={() => { setViewMode("logical"); setSelected(""); setScope("modules"); }}
                >
                  逻辑蓝图
                </button>
              </>
            )}
            <button
              className="agent-action"
              disabled={!snapshot || busy}
              title={scope !== "modules" && scope !== "all"
                ? "仅重新分析当前逻辑模块的下层内容，保留其他模块与上级结构。"
                : "重新构建全项目逻辑蓝图，并按功能域递归整理下层结构。"}
              onClick={() => void buildBlueprint()}
            >
              {scope !== "modules" && scope !== "all" && semanticGraph?.nodes.some((node) => node.id === scope && node.kind === "logical_module")
                ? "重建当前模块"
                : "AI 构建逻辑蓝图"}
            </button>
            <span>
              {snapshot.project_name} ·{" "}
              {snapshot.git_head?.slice(0, 10) || "无 Git HEAD"}
            </span>
            <button
              disabled={!editable || !history.past.length}
              onClick={() => {
                dispatch({ type: "undo" });
                setReport(undefined);
              }}
            >
              撤销
            </button>
            <button
              disabled={!editable || !history.future.length}
              onClick={() => {
                dispatch({ type: "redo" });
                setReport(undefined);
              }}
            >
              重做
            </button>
            <button
              disabled={!editable}
              onClick={() =>
                void run(async () => {
                  const data = await api<{ revision: number }>(
                    `/api/snapshots/${snapshot.id}/draft`,
                    {
                      method: "PUT",
                      body: JSON.stringify({
                        graph: history.present,
                        revision,
                      }),
                    },
                  );
                  setRevision(data.revision);
                  setInfo(`草稿已保存 r${data.revision}`);
                })
              }
            >
              保存草稿
            </button>
            <button
              disabled={!editable}
              onClick={() =>
                void run(async () => {
                  const saved = await api<{ graph: Graph; revision: number }>(
                    `/api/snapshots/${snapshot.id}/draft`,
                  );
                  dispatch({ type: "reset", graph: saved.graph });
                  setRevision(saved.revision);
                  setReport(undefined);
                })
              }
            >
              载入草稿
            </button>
            <button
              disabled={!editable}
              onClick={() => {
                try {
                  const data = localStorage.getItem(
                    `bpreviewer.recovery.${snapshot.id}`,
                  );
                  if (data) edit(JSON.parse(data) as Graph);
                } catch (e) {
                  setError(String(e));
                }
              }}
            >
              恢复浏览器草稿
            </button>
            <button onClick={() => void review()}>生成变更需求</button>
          </nav>
          <main>
            <aside className="left-workspace">
              <section className="tree-panel">
                <div className="tree-heading">
                  <h3>项目目录</h3>
                  <span className="tree-count">{treeEntries.filter((entry) => entry.kind === "file").length}</span>
                </div>
                <input
                  className="tree-search"
                  aria-label="搜索项目文件"
                  placeholder="查找文件或目录…"
                  value={treeQuery}
                  onChange={(e) => setTreeQuery(e.target.value)}
                />
                <div className="tree-scroll" role="tree" aria-label="实际项目目录">
                  {visibleTreeEntries.map((entry) => {
                    const expanded = expandedDirs.includes(entry.path) || Boolean(treeQuery.trim());
                    if (entry.kind === "directory") {
                      return (
                        <button
                          type="button"
                          role="treeitem"
                          aria-expanded={expanded}
                          className="tree-row tree-folder"
                          style={{ paddingLeft: 8 + entry.depth * 12 }}
                          key={entry.path}
                          onClick={() => setExpandedDirs((previous) =>
                            previous.includes(entry.path)
                              ? previous.filter((item) => item !== entry.path)
                              : [...previous, entry.path]
                          )}
                          title={entry.path}
                        >
                          <span className="tree-chevron">{expanded ? "⌄" : "›"}</span>
                          <span className="tree-folder-glyph">{expanded ? "▾" : "▸"}</span>
                          <span className="tree-name">{entry.name}</span>
                        </button>
                      );
                    }
                    return (
                      <button
                        type="button"
                        role="treeitem"
                        className={"tree-row tree-file " + (selectedPath === entry.path ? "is-selected" : "")}
                        style={{ paddingLeft: 23 + entry.depth * 12 }}
                        key={entry.path}
                        onClick={() => openTreeFile(entry)}
                        title={entry.previewable ? entry.path : entry.path + " · 预览受限"}
                      >
                        <span className="tree-ext">{entry.extension ? entry.extension.slice(1).toUpperCase().slice(0, 4) : "FILE"}</span>
                        <span className="tree-name">{entry.name}</span>
                        {!entry.previewable && <span className="tree-lock">—</span>}
                      </button>
                    );
                  })}
                  {!treeEntries.length && <p className="tree-empty">扫描项目后显示实际目录结构。</p>}
                  {treeTruncated && <p className="tree-empty">目录较大，已按安全上限截断。</p>}
                  {treeQuery && !visibleTreeEntries.length && <p className="tree-empty">没有匹配的文件或目录。</p>}
                </div>
              </section>
              <section className="blueprint-controls">
                <h3>蓝图视图</h3>
                {viewMode === "logical" && mode === "As-is" && (
                  <div className="layer-switch" role="group" aria-label="逻辑蓝图内容层">
                    {(["logic", "evidence", "mixed"] as const).map((layer) => (
                      <button key={layer} type="button"
                        className={detailLayer === layer ? "active" : ""}
                        onClick={() => setDetailLayer(layer)}>
                        {{ logic: "逻辑层", evidence: "证据层", mixed: "混合层" }[layer]}
                      </button>
                    ))}
                  </div>
                )}
                {viewMode === "logical" && mode === "As-is" && (
                  <div className="blueprint-navigation">
                    <div className="blueprint-breadcrumb">
                      <button type="button" onClick={() => setScope("modules")}>功能域总览</button>
                      {scope !== "modules" && scope !== "all" && (() => {
                        const parents: ProjectNode[] = [];
                        let item = graph.nodes.find((node) => node.id === scope);
                        const visited = new Set<string>();
                        while (item && !visited.has(item.id)) {
                          parents.unshift(item);
                          visited.add(item.id);
                          item = graph.nodes.find((node) => node.id === item?.parent);
                        }
                        return parents.map((node) => (
                          <button type="button" key={node.id} onClick={() => setScope(node.id)} title={node.label}>
                            / {node.label}
                          </button>
                        ));
                      })()}
                    </div>
                    <label className="blueprint-toggle">
                      <input type="checkbox" checked={showDependencies}
                        onChange={(event) => setShowDependencies(event.target.checked)} />
                      显示静态导入与归属连线
                    </label>
                    <p>单击查证据，双击逻辑模块进入内部；无下层内容时可在当前层点击 AI 细化。</p>
                  </div>
                )}
                <input
                  aria-label="搜索节点"
                  placeholder="搜索蓝图节点…"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
              <select
                aria-label="视图层级"
                value={scope}
                onChange={(e) => setScope(e.target.value)}
              >
                <option value="modules">功能域概览</option>
                <option value="all">全部节点</option>
                {graph.nodes
                  .filter((node) =>
                    node.kind === "logical_module" &&
                    graph.nodes.some((child) => child.parent === node.id)
                  )
                  .map((node) => {
                    let depth = 0;
                    let parent = node.parent;
                    const seen = new Set<string>();
                    while (parent && !seen.has(parent)) {
                      seen.add(parent);
                      depth += 1;
                      parent = graph.nodes.find((item) => item.id === parent)?.parent || "";
                    }
                    return (
                      <option key={node.id} value={node.id}>
                        {"　".repeat(Math.min(depth, 6))}{node.label}
                      </option>
                    );
                  })}
              </select>
              <p>
                当前可见 {visible.length} / 总计 {graph.nodes.length} 节点 · {graph.edges.length} 关系
              </p>
              {viewMode === "logical" && mode === "As-is" && scope !== "modules" && scope !== "all" && visible.length === 0 && (
                <div className="empty-layer">
                  <p>{detailLayer === "logic" ? "此模块尚无更细的语义子模块，可切换证据层查看源码。" : "当前层没有该类证据节点。"}</p>
                  <button onClick={() => void buildBlueprint()} disabled={busy}>AI 细化当前模块</button>
                </div>
              )}
              {mode === "As-is" && (
                <button type="button" onClick={() => {
                  setLayoutOverrides({});
                  if (snapshot) localStorage.removeItem(`bpreviewer.layout.${snapshot.id}`);
                }}>恢复自动布局</button>
              )}
              {viewMode === "logical" && analysisMeta && (
                <p className="analysis-meta">
                  MiMo · {analysisMeta.model}
                  <br />
                  深度分析 {analysisMeta.analyzed_files} 个文件 · 全项目分组 {analysisMeta.project_domains ?? "—"} 个功能域
                  <br />
                  未深度分析的文件保留为下层证据
                  <br />
                  {analysisMeta.local_rebuild
                    ? `局部重建：${analysisMeta.scope_label || "当前模块"}`
                    : `递归整理功能域：${analysisMeta.recursive_domains_built ?? 0}/${analysisMeta.recursive_domains_total ?? "—"}`}
                  <br />
                  {analysisMeta.cache_hit ? "使用缓存结果" : "本次调用模型"}
                </p>
              )}
              <p>
                STATIC_INFERRED：静态候选
                <br />
                DECLARED：manifest 声明
                <br />
                RUN_OBSERVED：可核验运行事件
                <br />
                UNVERIFIED：待确认/草稿
              </p>
              <p>
                当前扫描不会生成运行事件。数据流需合同或声明证据；控制边表示调用候选。
              </p>
              {snapshot.warnings.map((w) => (
                <p className="warning" key={w}>
                  {w}
                </p>
              ))}
              <button
                disabled={!editable}
                onClick={() => {
                  const id = `proposed:${crypto.randomUUID()}`;
                  edit({
                    ...history.present,
                    nodes: [
                      ...history.present.nodes,
                      {
                        id,
                        label: "拟议节点",
                        kind: "proposed",
                        parent: "",
                        ports: [
                          { id: "A", io: "input" },
                          { id: "B", io: "input" },
                          { id: "out", io: "output" },
                        ],
                        parameters: {},
                        position: { x: 100, y: 100 },
                        evidence: [],
                        description: "",
                      },
                    ],
                  });
                  setScope("all");
                  setSelected(id);
                }}
              >
                添加拟议节点
              </button>
                <button onClick={layout}>布局 / 适应画布</button>
              </section>
            </aside>
            <section className="canvas" aria-label="项目蓝图">
              {mode === "Diff" ? (
                <div className="diff">
                  <h2>Change Request</h2>
                  <p>
                    {report?.json.operations.length || 0}{" "}
                    项语义变更；画布坐标变化不计入需求。
                  </p>
                  <button disabled={!report} onClick={() => download("json")}>
                    导出 JSON
                  </button>
                  <button
                    disabled={!report}
                    onClick={() => download("markdown")}
                  >
                    导出 Markdown
                  </button>
                  <pre>{report?.markdown}</pre>
                </div>
              ) : (
                <ReactFlow
                  colorMode={darkMode ? "dark" : "light"}
                  nodes={nodes}
                  edges={edges}
                  nodeTypes={nodeTypes}
                  fitView
                  minZoom={0.05}
                  nodesDraggable={mode === "As-is" || editable}
                  nodesConnectable={editable}
                  edgesReconnectable={editable}
                  deleteKeyCode={null}
                  onNodeClick={(_, n) => {
                    setSelected(n.id);
                    setEdgeId("");
                  }}
                  onNodeDoubleClick={(_, node) => {
                    if (node.data.item.kind === "logical_module") {
                      setScope(node.id);
                      setQuery("");
                      setSelected("");
                      setEdgeId("");
                    }
                  }}
                  onEdgeClick={(_, e) => {
                    setEdgeId(e.id);
                    setSelected("");
                  }}
                  onNodesChange={moveNodes}
                  onNodeDragStop={(_, n) => {
                    if (editable) {
                      edit({
                        ...history.present,
                        nodes: history.present.nodes.map((v) =>
                          v.id === n.id ? { ...v, position: n.position } : v,
                        ),
                      });
                    } else if (mode === "As-is" && snapshot) {
                      const updated = { ...layoutOverrides, [n.id]: n.position };
                      setLayoutOverrides(updated);
                      localStorage.setItem(`bpreviewer.layout.${snapshot.id}`, JSON.stringify(updated));
                    }
                  }}
                  onConnect={connect}
                  onReconnect={(old, c) => connect(c, old.id)}
                >
                  <Background />
                  <Controls />
                  <MiniMap />
                </ReactFlow>
              )}
            </section>
            <aside className="inspector">
              <h3>Inspector</h3>
              {selectedPath && (
                <div className="selected-source">
                  <div className="source-heading">
                    <span>当前文件</span>
                    <small>{selectedPath}</small>
                  </div>
                  {source && <pre className="source-preview">{source}</pre>}
                </div>
              )}
              {current ? (
                <>
                  <strong>{current.label}</strong>
                  <p>{current.description}</p>
                  <code>{current.id}</code>
                  <p>证据与实现位置</p>
                  {current.evidence.map((p, i) => (
                    <button
                      key={i}
                      onClick={() => void showSource(p)}
                      title={p.sha256}
                    >
                      {p.path}:{p.line} · {p.status}
                    </button>
                  ))}
                  <label>
                    端口（JSON）
                    <textarea
                      aria-label="端口 JSON"
                      readOnly={!editable}
                      value={portsText}
                      onChange={(e) => setPortsText(e.target.value)}
                    />
                  </label>
                  <label>
                    参数（JSON）
                    <textarea
                      aria-label="参数 JSON"
                      readOnly={!editable}
                      value={paramsText}
                      onChange={(e) => setParamsText(e.target.value)}
                    />
                  </label>
                  {viewMode === "logical" && mode === "As-is" && current.kind === "logical_module" && (
                    <button
                      disabled={busy}
                      onClick={() => void rebuildScope(current.id)}
                      title="只重新分析当前模块内部的文件与子模块，不修改其他分支。"
                    >
                      AI 重建此模块内部
                    </button>
                  )}
                  <button
                    disabled={!editable}
                    onClick={() => {
                      try {
                        const ports = JSON.parse(portsText) as Port[],
                          parameters = JSON.parse(paramsText) as Record<
                            string,
                            unknown
                          >;
                        if (
                          !Array.isArray(ports) ||
                          ports.some(
                            (p) => !p.id || !["input", "output"].includes(p.io),
                          ) ||
                          new Set(ports.map((p) => p.id)).size !== ports.length
                        )
                          throw new Error(
                            "端口 ID 须唯一且包含 input/output 方向",
                          );
                        edit({
                          ...history.present,
                          nodes: history.present.nodes.map((n) =>
                            n.id === current.id
                              ? { ...n, ports, parameters }
                              : n,
                          ),
                        });
                      } catch (e) {
                        setError(String(e));
                      }
                    }}
                  >
                    应用到草稿
                  </button>
                  <button
                    disabled={!editable}
                    onClick={() => {
                      edit({
                        nodes: history.present.nodes.filter(
                          (n) => n.id !== current.id,
                        ),
                        edges: history.present.edges.filter(
                          (e) =>
                            e.source !== current.id && e.target !== current.id,
                        ),
                      });
                      setSelected("");
                    }}
                  >
                    删除拟议节点
                  </button>
                </>
              ) : (
                <p>选择蓝图节点查看功能、端口和源码证据。点击左侧文件可查看真实文件内容；双击节点可展开下级。</p>
              )}
              <label>
                新增 / 重连关系
                <select
                  disabled={!editable}
                  value={edgeKind}
                  onChange={(e) =>
                    setEdgeKind(e.target.value as "data" | "control")
                  }
                >
                  <option value="data">数据流</option>
                  <option value="control">控制依赖</option>
                </select>
              </label>
              {pending && (
                <p>
                  已选输出 {pending.id}:{pending.port}
                </p>
              )}
              {edgeId && (
                <>
                  <pre>
                    {JSON.stringify(
                      graph.edges.find((e) => e.id === edgeId),
                      null,
                      2,
                    )}
                  </pre>
                  <button
                    disabled={!editable}
                    onClick={() => {
                      edit({
                        ...history.present,
                        edges: history.present.edges.filter(
                          (e) => e.id !== edgeId,
                        ),
                      });
                      setEdgeId("");
                    }}
                  >
                    断开拟议连线
                  </button>
                </>
              )}
              <button
                disabled={!snapshot}
                onClick={() =>
                  void run(async () =>
                    setInfo(
                      JSON.stringify(
                        await api(`/api/snapshots/${snapshot.id}/validate`, {
                          method: "POST",
                          body: JSON.stringify({ graph: history.present }),
                        }),
                      ),
                    ),
                  )
                }
              >
                检查草稿约束
              </button>
            </aside>
          </main>
        </>
      ) : (
        <section className="welcome">
          <h2>理解项目，再提出变更</h2>
          <p>
            选择独立的本地 Python
            项目，查看模块、函数、静态调用与类型化端口。As-is 保留证据，Proposed
            保存你的方案，Diff 导出可审阅需求。
          </p>
        </section>
      )}
    </div>
  );
}
