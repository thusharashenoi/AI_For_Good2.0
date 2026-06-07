import React, { useEffect, useRef, useState } from "react";
import { DataSet } from "vis-data";
import { Network } from "vis-network";
import { shortId } from "./api.js";
import "vis-network/styles/vis-network.min.css";

const GROUP_COLORS = {
  "O-": "#e11d48", "O+": "#fb7185", "A-": "#2563eb", "A+": "#60a5fa",
  "B-": "#d97706", "B+": "#f59e0b", "AB-": "#7c3aed", "AB+": "#a78bfa",
};

export function tooltipLines(node) {
  if (!node) return [];
  const lines = [node.label || "Unknown"];
  if (node.group) lines.push(`Blood group: ${node.group}`);
  if (node.city) lines.push(`City: ${node.city}`);
  if (node.kind === "patient") {
    if (node.reason === "unbridged") lines.push("Status: unbridged");
    if (node.reason === "under_strength") lines.push(`Bridge active: ${node.activeCount ?? "?"}/6`);
    if (node.reason === "bridge_center") lines.push("Bridge patient (center)");
    if (node.reason === "patient_focus") lines.push("Patient focus");
    if (node.bridgeId) lines.push(`Bridge: ${shortId(node.bridgeId)}`);
  }
  if (node.kind === "donor") {
    if (node.role === "free_pool") lines.push("Free pool donor (not in any bridge)");
    if (node.role === "active") lines.push("Bridge member · active slot");
    if (node.role === "buffer") lines.push("Bridge member · buffer slot");
    if (node.role === "bridge") lines.push("Bridge member");
    if (node.role === "candidate") {
      const hint = node.slotTypeHint ? ` · ${node.slotTypeHint} slot` : "";
      lines.push(`Ranked candidate (free pool)${hint}`);
    }
    if (node.slotId) lines.push(`Slot: ${node.slotId}`);
    if (node.backupFor) lines.push(`Backup for slot ${node.backupFor}`);
    if (node.showRate != null) lines.push(`Show-up: ${Math.round(node.showRate * 100)}%`);
  }
  return lines;
}

function GraphTooltip({ node, x, y }) {
  if (!node) return null;
  const lines = tooltipLines(node);
  return (
    <div
      className="absolute z-20 pointer-events-none max-w-[220px] rounded-lg border border-line bg-surface px-3 py-2 shadow-lg text-xs text-ink overflow-hidden"
      style={{ left: x + 14, top: y + 14 }}
    >
      <p className="font-semibold leading-snug break-words">{lines[0]}</p>
      {lines.slice(1).map((line) => (
        <p key={line} className="text-muted leading-snug mt-0.5 break-all">{line}</p>
      ))}
    </div>
  );
}

function toVisNodes(nodes, showDonorLabels = false, highlightNodeId = null) {
  return nodes.map((n) => {
    const selected = n.kind === "patient" && n.id === highlightNodeId;
    return {
      id: n.id,
      label: n.kind === "patient" || showDonorLabels ? n.label : "",
      shape: n.kind === "patient" ? "star" : "dot",
      size: n.kind === "patient" ? (selected ? 30 : 26) : 10 + (n.showRate ?? 0.8) * 10,
      color: {
        background: GROUP_COLORS[n.group] || "#9ca3af",
        border: selected ? "#f14164" : n.kind === "patient" ? "#141414" : "#ffffff",
        highlight: { background: GROUP_COLORS[n.group] || "#9ca3af", border: "#f14164" },
      },
      font: {
        size: n.kind === "patient" ? 12 : showDonorLabels ? 10 : 0,
        color: "#141414",
        face: "Manrope, sans-serif",
        strokeWidth: n.kind === "patient" ? 3 : 0,
        strokeColor: "#ffffff",
        bold: selected,
      },
      borderWidth: selected ? 3.5 : n.kind === "patient" ? 2.5 : 1.5,
      x: n.x,
      y: n.y,
      fixed: n.x != null && n.y != null,
      _raw: n,
    };
  });
}

function toVisEdges(edges) {
  return edges.map((e) => {
    const isBridge = e.kind === "bridge";
    const isCandidate = e.kind === "candidate";
    return {
      from: e.from,
      to: e.to,
      value: e.score,
      color: {
        color: isBridge ? "rgba(241,65,100,0.55)" : isCandidate ? "rgba(245,158,11,0.45)" : "rgba(148,163,184,0.35)",
        highlight: "#f14164",
        hover: "#f14164",
      },
      width: isBridge ? 2.2 : isCandidate ? 1.4 : 0.6 + e.score * 1.2,
      dashes: isCandidate,
      smooth: { type: "continuous", roundness: 0.35 },
    };
  });
}

export default function GraphNetwork({
  data, layout = "force", height = "420px", onNodeClick, highlightNodeId = null,
  disableZoom = false,
}) {
  const ref = useRef(null);
  const wrapRef = useRef(null);
  const netRef = useRef(null);
  const nodesRef = useRef(null);
  const [hoveredNode, setHoveredNode] = useState(null);
  const [tooltipPos, setTooltipPos] = useState({ x: 0, y: 0 });

  useEffect(() => {
    if (!ref.current || !data?.nodes?.length) return;

    const nodes = new DataSet(toVisNodes(data.nodes, layout === "fixed", highlightNodeId));
    nodesRef.current = nodes;
    const edges = new DataSet(toVisEdges(data.edges || []));

    const options = {
      nodes: { shadow: { enabled: true, size: 6, x: 1, y: 2, color: "rgba(0,0,0,0.12)" } },
      edges: { selectionWidth: 2, hoverWidth: 1.6 },
      interaction: {
        hover: true,
        tooltipDelay: 60,
        hideEdgesOnDrag: true,
        navigationButtons: !disableZoom,
        keyboard: false,
        zoomView: !disableZoom,
      },
      physics: layout === "force" ? {
        enabled: true,
        barnesHut: {
          gravitationalConstant: -8000,
          centralGravity: 0.25,
          springLength: 160,
          springConstant: 0.04,
          damping: 0.55,
          avoidOverlap: 0.6,
        },
        stabilization: { iterations: 200 },
      } : { enabled: false },
    };

    if (netRef.current) {
      netRef.current.destroy();
      netRef.current = null;
    }

    const network = new Network(ref.current, { nodes, edges }, options);
    netRef.current = network;

    const onMove = (ev) => {
      const rect = wrapRef.current?.getBoundingClientRect();
      if (!rect) return;
      setTooltipPos({ x: ev.clientX - rect.left, y: ev.clientY - rect.top });
    };

    network.on("hoverNode", (params) => {
      const node = nodes.get(params.node);
      setHoveredNode(node?._raw || null);
    });
    network.on("blurNode", () => setHoveredNode(null));

    if (onNodeClick) {
      network.on("click", (params) => {
        if (!params.nodes.length) return;
        const node = nodes.get(params.nodes[0]);
        onNodeClick(node?._raw);
      });
    }

    wrapRef.current?.addEventListener("mousemove", onMove);

    return () => {
      wrapRef.current?.removeEventListener("mousemove", onMove);
      network.destroy();
      netRef.current = null;
      nodesRef.current = null;
      setHoveredNode(null);
    };
  }, [data, layout, onNodeClick, disableZoom]);

  useEffect(() => {
    if (!nodesRef.current || !data?.nodes?.length) return;
    data.nodes.forEach((n) => {
      if (n.kind !== "patient") return;
      const selected = n.id === highlightNodeId;
      nodesRef.current.update({
        id: n.id,
        size: selected ? 30 : 26,
        borderWidth: selected ? 3.5 : 2.5,
        font: { bold: selected },
        color: {
          background: GROUP_COLORS[n.group] || "#9ca3af",
          border: selected ? "#f14164" : "#141414",
          highlight: { background: GROUP_COLORS[n.group] || "#9ca3af", border: "#f14164" },
        },
      });
    });
  }, [highlightNodeId, data]);

  if (!data?.nodes?.length) {
    return (
      <div className="flex items-center justify-center text-muted text-sm rounded-xl bg-canvas border border-line"
        style={{ height }}>
        No graph data yet.
      </div>
    );
  }

  return (
    <div ref={wrapRef} className="relative rounded-xl border border-line overflow-hidden bg-canvas shadow-inner"
      style={{ height }}>
      <div ref={ref} className="w-full h-full" />
      <GraphTooltip node={hoveredNode} x={tooltipPos.x} y={tooltipPos.y} />
    </div>
  );
}

export function GraphLegend({ compact = false }) {
  const items = Object.entries(GROUP_COLORS);
  return (
    <div className={`flex flex-wrap gap-x-4 gap-y-1 ${compact ? "text-[10px]" : "text-xs"} text-muted`}>
      {items.map(([g, c]) => (
        <span key={g} className="inline-flex items-center gap-1">
          <span className="inline-block w-2.5 h-2.5 rounded-full border border-line" style={{ background: c }} />
          {g}
        </span>
      ))}
      <span className="inline-flex items-center gap-1 ml-2">★ patient</span>
      <span className="inline-flex items-center gap-1">● donor</span>
    </div>
  );
}
