/* The small architecture diagrams on the agentic cards, shared by index.html and
   agentic.html. Each card holds <svg class="mini" data-n="11"></svg>; this fills it. */

// One diagram per architecture: nodes are [kind, x, y, label]. Kinds: io, llm, jev, code, human. Edges: [from, to, style].
const ARCH = [
  { n: "11", arch: "Sequential",
    nodes: [["io", 16, 50, ""], ["llm", 62, 50, "A"], ["jev", 108, 50], ["llm", 154, 50, "A"], ["jev", 200, 50], ["llm", 246, 50, "A"], ["jev", 290, 50], ["human", 200, 104, ""], ["code", 330, 50, ""]],
    edges: [[0, 1], [1, 2], [2, 3], [3, 4], [4, 5], [5, 6], [6, 8], [2, 7, "d"], [4, 7, "d"], [6, 7, "d"], [2, 1, "back"], [4, 3, "back"], [6, 5, "back"]] },
  { n: "12", arch: "Parallel",
    nodes: [["io", 16, 60, ""], ["jev", 70, 60], ["llm", 170, 14, "A"], ["llm", 170, 45, "A"], ["llm", 170, 76, "A"], ["llm", 170, 107, "A"], ["jev", 270, 60], ["code", 330, 60, ""]],
    edges: [[0, 1], [1, 2], [1, 3], [1, 4, "d"], [1, 5], [2, 6], [3, 6], [5, 6], [6, 7]] },
  { n: "13", arch: "Orchestrator–workers",
    nodes: [["io", 16, 60, ""], ["jev", 110, 60], ["llm", 230, 12, "A"], ["llm", 260, 44, "A"], ["llm", 260, 78, "A"], ["llm", 230, 110, "A"], ["code", 330, 40, ""], ["human", 330, 84, ""]],
    edges: [[0, 1], [1, 2, "both"], [1, 3, "both"], [1, 4, "both"], [1, 5, "both"], [1, 6, "d"], [1, 7, "d"]] },
  { n: "14", arch: "Router / handoff",
    nodes: [["io", 16, 60, ""], ["jev", 90, 60], ["llm", 200, 14, "A"], ["llm", 200, 45, "A"], ["llm", 200, 76, "A"], ["llm", 200, 107, "A"], ["human", 310, 60, ""]],
    edges: [[0, 1], [1, 2], [1, 3, "d"], [1, 4], [1, 5, "d"], [1, 6, "d"]] },
  { n: "15", arch: "Evaluator–optimizer",
    nodes: [["io", 16, 50, ""], ["llm", 90, 50, "A"], ["jev", 200, 50], ["code", 310, 22, ""], ["human", 310, 80, ""]],
    edges: [[0, 1], [1, 2], [2, 3], [2, 4, "d"], [2, 1, "loop"]] },
  { n: "16", arch: "Tool-calling agent",
    nodes: [["io", 16, 50, ""], ["llm", 90, 50, "A"], ["jev", 200, 50], ["code", 310, 22, ""], ["human", 310, 80, ""]],
    edges: [[0, 1], [1, 2], [2, 3], [2, 4, "d"], [3, 1, "loop"]] },
];

const COLOUR = { io: "--line", llm: "--llm", jev: "--jev", code: "--code", human: "--human" };
function mini(svgEl, a) {
  const svg = d3.select(svgEl).attr("viewBox", "0 0 346 124");
  const id = "m" + a.n;
  svg.append("defs").append("marker").attr("id", id).attr("viewBox", "0 0 8 8").attr("refX", 7).attr("refY", 4)
    .attr("markerWidth", 5).attr("markerHeight", 5).attr("orient", "auto")
    .append("path").attr("d", "M0,0 L8,4 L0,8 Z").attr("fill", "var(--muted)");
  const P = a.nodes;
  a.edges.forEach(([f, t, style]) => {
    const [x1, y1] = [P[f][1], P[f][2]], [x2, y2] = [P[t][1], P[t][2]];
    let d;
    if (style === "back") d = `M${x1},${y1 - 10} C${x1},${y1 - 34} ${x2},${y2 - 34} ${x2},${y2 - 12}`;
    else if (style === "loop") d = `M${x1},${y1 + 12} C${x1},${y1 + 52} ${x2},${y2 + 52} ${x2},${y2 + 12}`;
    else { const mx = (x1 + x2) / 2; d = `M${x1 + 12},${y1} C${mx},${y1} ${mx},${y2} ${x2 - 13},${y2}`; }
    svg.append("path").attr("d", d).attr("fill", "none").attr("stroke", "var(--muted)").attr("stroke-opacity", .55)
      .attr("stroke-width", 1.3).attr("stroke-dasharray", style === "d" || style === "back" || style === "loop" ? "3 3" : null)
      .attr("marker-end", `url(#${id})`).attr("marker-start", style === "both" ? `url(#${id})` : null);
  });
  P.forEach(([kind, x, y]) => {
    const c = `var(${COLOUR[kind]})`;
    if (kind === "jev") svg.append("rect").attr("x", x - 10).attr("y", y - 10).attr("width", 20).attr("height", 20).attr("rx", 3)
      .attr("transform", `rotate(45 ${x} ${y})`).attr("fill", c);
    else if (kind === "llm") svg.append("rect").attr("x", x - 13).attr("y", y - 10).attr("width", 26).attr("height", 20).attr("rx", 4)
      .attr("fill", "var(--panel)").attr("stroke", c).attr("stroke-width", 1.6);
    else svg.append("circle").attr("cx", x).attr("cy", y).attr("r", kind === "io" ? 7 : 9)
      .attr("fill", kind === "io" ? "var(--bg)" : c).attr("stroke", kind === "io" ? "var(--muted)" : "none");
  });
}

/** Draws every svg.mini[data-n] on the page. Needs the global d3 from d3-mini.js. */
export function drawMiniDiagrams(root = document) {
  ARCH.forEach((a) => { const el = root.querySelector(`svg.mini[data-n="${a.n}"]`); if (el) mini(el, a); });
}
