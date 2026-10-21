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
} from "./types";
import "@xyflow/react/dist/style.css";

type CardData = {
  item: ProjectNode;
  editable: boolean;
  choose: (id: string, p: Port) => void;
};
function Card({ data }: NodeProps<Node<CardData>>) {
  return (
    <article className="card">
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
            title={p.description || "端口：类型未知时须人工确认"}
          />
          <button
            className="nodrag"
            onClick={() => data.choose(data.item.id, p)}
            title={`${p.description || "输入/输出端口"}\n类型: ${p.data_type || "未知/待确认"}\n合同: ${p.schema_ref || "未声明"}\n基数: ${p.multiplicity || "many"}`}
          >
            {p.label || p.id}
            <em>{p.data_type || "未知 / 待确认"}</em>
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
  const [path, setPath] = useState("");
  const [roots, setRoots] = useState<string[]>([]);
  const [snapshot, setSnapshot] = useState<Snapshot>();
  const [mode, setMode] = useState<"As-is" | "Proposed" | "Diff">("As-is");
  const [history, dispatch] = useReducer(historyReducer, {
    past: [],
    present: empty,
    future: [],
  });
  const [scope, setScope] = useState("modules");
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
  const graph = mode === "As-is" ? snapshot?.graph || empty : history.present;
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
    const primary = graph.nodes.filter(
      (n) =>
        (scope === "all" ||
          (scope === "modules"
            ? !n.parent
            : n.id === scope || n.parent === scope)) &&
        (!query || n.label.toLowerCase().includes(query.toLowerCase())),
    );
    return primary;
  }, [graph, scope, query]);
  const renderedNodes: Node[] = visible.map((n, i) => ({
    id: n.id,
    type: "project",
    position:
      n.position.x || n.position.y
        ? n.position
        : { x: (i % 3) * 360, y: Math.floor(i / 3) * 290 },
    selected: n.id === selected,
    data: { item: n, editable, choose: portClick },
  }));
  const [canvasNodes, setCanvasNodes] = useState<Node[]>([]);
  useEffect(() => {
    setCanvasNodes(renderedNodes);
  }, [graph, scope, query, selected, editable, pending]);
  const nodes = canvasNodes;
  const moveNodes = (changes: NodeChange[]) =>
    setCanvasNodes((previous) => applyNodeChanges(changes, previous));
  const ids = new Set(visible.map((n) => n.id));
  const edges = graph.edges
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
        stroke: e.kind === "data" ? "#408e90" : "#74849a",
        strokeDasharray: e.kind === "control" ? "6 4" : undefined,
      },
    }));
  useEffect(() => {
    setPortsText(current ? JSON.stringify(current.ports, null, 2) : "");
    setParamsText(current ? JSON.stringify(current.parameters, null, 2) : "");
    setSource("");
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
      const saved = await api<{ graph: Graph; revision: number }>(
        `/api/snapshots/${data.id}/draft`,
      );
      setSnapshot(data);
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
  const showSource = (p: Evidence) =>
    run(async () => {
      const data = await api<{
        content: string;
        first_line: number;
        stale: boolean;
      }>(
        `/api/snapshots/${snapshot!.id}/source?path=${encodeURIComponent(p.path)}&line=${p.line}`,
      );
      setSource(
        `${p.path}:${data.first_line}${data.stale ? " · 源码已变化，需重新扫描" : ""}\n${data.content}`,
      );
    });
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
                onClick={() => (m === "Diff" ? void review() : setMode(m))}
              >
                {m}
              </button>
            ))}
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
            <aside>
              <h3>项目结构</h3>
              <input
                aria-label="搜索节点"
                placeholder="搜索节点"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
              <select
                aria-label="视图层级"
                value={scope}
                onChange={(e) => setScope(e.target.value)}
              >
                <option value="modules">模块概览</option>
                <option value="all">全部节点</option>
                {graph.nodes
                  .filter((n) => !n.parent)
                  .map((n) => (
                    <option key={n.id} value={n.id}>
                      {n.label}
                    </option>
                  ))}
              </select>
              <p>
                节点 {graph.nodes.length} · 关系 {graph.edges.length}
              </p>
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
                  nodes={nodes}
                  edges={edges}
                  nodeTypes={nodeTypes}
                  fitView
                  minZoom={0.05}
                  nodesDraggable={editable}
                  nodesConnectable={editable}
                  edgesReconnectable={editable}
                  deleteKeyCode={null}
                  onNodeClick={(_, n) => {
                    setSelected(n.id);
                    setEdgeId("");
                  }}
                  onNodeDoubleClick={(_, n) => setScope(n.id)}
                  onEdgeClick={(_, e) => {
                    setEdgeId(e.id);
                    setSelected("");
                  }}
                  onNodesChange={moveNodes}
                  onNodeDragStop={(_, n) => {
                    if (editable)
                      edit({
                        ...history.present,
                        nodes: history.present.nodes.map((v) =>
                          v.id === n.id ? { ...v, position: n.position } : v,
                        ),
                      });
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
                  {source && <pre>{source}</pre>}
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
                <p>选择节点查看端口、合同和源码证据。双击模块可展开函数。</p>
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
