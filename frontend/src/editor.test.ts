import { describe, expect, it } from "vitest";
import { connectionError, historyReducer } from "./editor";
import type { Graph, Link } from "./types";
const graph: Graph = {
  nodes: [
    {
      id: "s",
      label: "source",
      kind: "function",
      parent: "",
      ports: [{ id: "out", io: "output" }],
      parameters: {},
      evidence: [],
      position: { x: 0, y: 0 },
      description: "",
    },
    {
      id: "t",
      label: "target",
      kind: "function",
      parent: "",
      ports: [
        { id: "A", io: "input", multiplicity: "one" },
        { id: "B", io: "input", multiplicity: "one" },
      ],
      parameters: {},
      evidence: [],
      position: { x: 0, y: 0 },
      description: "",
    },
  ],
  edges: [],
};
const edge: Link = {
  id: "e",
  source: "s",
  source_port: "out",
  target: "t",
  target_port: "A",
  kind: "data",
  status: "UNVERIFIED",
  evidence: [],
};
describe("generic workbench history and multi-port connections", () => {
  it("supports unknown ports without claiming contract compatibility", () =>
    expect(connectionError(graph, edge, true)).toBeNull());
  it("rewires A to B and preserves undo/redo", () => {
    const initial = {
      past: [],
      present: { ...graph, edges: [edge] },
      future: [],
    };
    const changed = { ...graph, edges: [{ ...edge, target_port: "B" }] };
    const edited = historyReducer(initial, { type: "edit", graph: changed });
    expect(
      historyReducer(edited, { type: "undo" }).present.edges[0].target_port,
    ).toBe("A");
    expect(
      historyReducer(historyReducer(edited, { type: "undo" }), { type: "redo" })
        .present.edges[0].target_port,
    ).toBe("B");
  });
  it("rejects wrong direction and single-input duplicates", () => {
    expect(
      connectionError(graph, { ...edge, source_port: "missing" }, true),
    ).toBeTruthy();
    expect(
      connectionError(
        { ...graph, edges: [edge] },
        { ...edge, id: "new" },
        true,
      ),
    ).toBeTruthy();
  });
  it("rejects incompatible declared contracts", () => {
    const typed = structuredClone(graph);
    typed.nodes[0].ports[0].data_type = "number";
    typed.nodes[1].ports[0].data_type = "text";
    expect(connectionError(typed, edge, true)).toContain("类型");
  });
});
