import {
  fireEvent,
  render,
  screen,
  waitFor,
  cleanup,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { App } from "./App";
vi.mock("@xyflow/react", () => ({
  ReactFlowProvider: ({ children }: { children: React.ReactNode }) => (
    <>{children}</>
  ),
  useReactFlow: () => ({ fitView: vi.fn() }),
  ReactFlow: ({
    nodes,
  }: {
    nodes: { id: string; data: { item: { label: string } } }[];
  }) => (
    <div>
      {nodes.map((n) => (
        <span key={n.id}>{n.data.item.label}</span>
      ))}
    </div>
  ),
  Background: () => null,
  Controls: () => null,
  MiniMap: () => null,
  Handle: () => null,
  Position: { Left: "left", Right: "right" },
  MarkerType: { ArrowClosed: "arrow" },
}));
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  sessionStorage.clear();
  localStorage.clear();
});
it("opens a generic project with a read-only baseline then isolated proposed mode", async () => {
  const node = {
    id: "module",
    label: "Synthetic module",
    kind: "module",
    parent: "",
    ports: [],
    evidence: [],
    position: { x: 0, y: 0 },
    parameters: {},
    description: "",
  };
  const graph = { nodes: [node], edges: [] };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => ({
      ok: true,
      json: async () =>
        path === "/api/projects"
          ? { allowed_roots: ["demo"] }
          : path.endsWith("/tree")
            ? { entries: [], truncated: false }
            : path.endsWith("/draft")
              ? { graph, revision: 0 }
            : {
                id: "sample",
                project_name: "demo",
                git_head: null,
                graph,
                warnings: [],
                require_dag: true,
              },
    })),
  );
  render(<App />);
  fireEvent.change(screen.getByLabelText("本地访问令牌"), {
    target: { value: "local-token" },
  });
  fireEvent.click(screen.getByText("连接服务"));
  await waitFor(() =>
    expect(screen.getByLabelText("项目目录")).toHaveValue("demo"),
  );
  fireEvent.click(screen.getByText("扫描项目"));
  await screen.findAllByText("Synthetic module");
  expect(screen.getByText("添加拟议节点")).toBeDisabled();
  fireEvent.click(screen.getByText("Proposed"));
  expect(screen.getByText("添加拟议节点")).not.toBeDisabled();
  fireEvent.click(screen.getByText("添加拟议节点"));
  expect(screen.getAllByText("拟议节点").length).toBeGreaterThan(0);
  expect(localStorage.getItem("bpreviewer.recovery.sample")).toContain(
    "proposed:",
  );
});
