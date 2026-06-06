import React, { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "./api.js";
import GraphNetwork, { GraphLegend } from "./GraphNetwork.jsx";

const POOL_FILTERS = [
  { id: "all", label: "All" },
  { id: "unbridged", label: "Unbridged" },
  { id: "under_strength", label: "Under-strength" },
];

function MetaPill({ label, value }) {
  return (
    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-canvas border border-line text-xs">
      <span className="text-muted">{label}</span>
      <span className="font-semibold text-ink">{value ?? "—"}</span>
    </span>
  );
}

function filterOverview(overview, filter) {
  if (!overview || filter === "all") return overview;
  const patients = overview.nodes.filter((n) => n.kind === "patient" && n.reason === filter);
  const patientIds = new Set(patients.map((n) => n.id));
  const donorIds = new Set();
  const edges = overview.edges.filter((e) => {
    if (!patientIds.has(e.from)) return false;
    donorIds.add(e.to);
    return true;
  });
  const nodes = overview.nodes.filter((n) => patientIds.has(n.id) || donorIds.has(n.id));
  return { ...overview, nodes, edges };
}

export default function BloodGraphTab({ selectedBridgeId, onSelectBridge, onClearBridge }) {
  const [overview, setOverview] = useState(null);
  const [focusGraph, setFocusGraph] = useState(null);
  const [focusKind, setFocusKind] = useState(null);
  const [poolFilter, setPoolFilter] = useState("all");
  const [loadingOverview, setLoadingOverview] = useState(true);
  const [loadingFocus, setLoadingFocus] = useState(false);
  const [error, setError] = useState(null);
  const [selectedPatient, setSelectedPatient] = useState(null);

  const loadOverview = useCallback(async () => {
    setLoadingOverview(true);
    setError(null);
    try {
      setOverview(await api.graphOverview(8));
    } catch (e) {
      setError(String(e));
    } finally {
      setLoadingOverview(false);
    }
  }, []);

  useEffect(() => { loadOverview(); }, [loadOverview]);

  const filteredOverview = useMemo(
    () => filterOverview(overview, poolFilter),
    [overview, poolFilter],
  );

  const filterCounts = useMemo(() => ({
    all: overview?.meta?.targetPatients ?? 0,
    unbridged: overview?.meta?.unbridgedPatients ?? 0,
    under_strength: overview?.meta?.underStrengthPatients ?? 0,
  }), [overview]);

  const loadFocus = useCallback(async (kind, id) => {
    setLoadingFocus(true);
    setError(null);
    setFocusGraph(null);
    try {
      const data = kind === "bridge"
        ? await api.graphBridge(id, 14)
        : await api.graphPatient(id, 14);
      if (data.error) throw new Error(data.error);
      setFocusGraph(data);
      setFocusKind(kind);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoadingFocus(false);
    }
  }, []);

  useEffect(() => {
    if (selectedBridgeId) {
      setSelectedPatient(null);
      loadFocus("bridge", selectedBridgeId);
    }
  }, [selectedBridgeId, loadFocus]);

  const handleNodeClick = useCallback((node) => {
    if (!node || node.kind !== "patient") return;
    setSelectedPatient(node);
    if (node.reason === "under_strength" && node.bridgeId) {
      onSelectBridge?.(node.bridgeId);
      loadFocus("bridge", node.bridgeId);
    } else {
      onSelectBridge?.(null);
      loadFocus("patient", node.patientId);
    }
  }, [loadFocus, onSelectBridge]);

  function clearFocus() {
    setSelectedPatient(null);
    setFocusGraph(null);
    setFocusKind(null);
    onClearBridge?.();
  }

  const focusTitle = focusKind === "bridge"
    ? "Bridge constellation"
    : "Patient donor pool";

  const focusSub = focusKind === "bridge"
    ? "Patient at the center · inner ring = confirmed bridge members · outer dashed = ranked free-pool candidates."
    : "Patient at the center · dashed ring = top-ranked free-pool donors to form a new bridge (6 active + 4 buffer).";

  const highlightPatientId = useMemo(() => {
    if (selectedPatient?.id) return selectedPatient.id;
    if (focusGraph?.patientId) return `P:${focusGraph.patientId}`;
    return null;
  }, [selectedPatient, focusGraph]);

  return (
    <div className="space-y-6">
      {error && (
        <div className="card p-4 border-brand/30 bg-brand-soft text-danger text-sm">{error}</div>
      )}

      <section className="card p-5">
        <div className="flex flex-wrap items-start justify-between gap-3 mb-3">
          <div>
            <h2 className="font-head font-semibold text-ink">Blood Graph · open pool</h2>
            <p className="text-xs text-muted mt-0.5 max-w-2xl">
              Free-pool donors linked to patients who need bridges. Click a patient star to
              focus them in the panel below.
            </p>
          </div>
          <button onClick={loadOverview} disabled={loadingOverview}
            className="px-3 py-1.5 rounded-lg border border-line bg-surface hover:bg-canvas text-xs font-semibold text-ink disabled:opacity-50">
            {loadingOverview ? "Loading…" : "Reload graph"}
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-2 mb-3">
          <div className="flex rounded-xl border border-line bg-canvas p-1">
            {POOL_FILTERS.map(({ id, label }) => (
              <button key={id} onClick={() => setPoolFilter(id)}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
                  poolFilter === id ? "bg-brand text-white shadow-sm" : "text-muted hover:text-ink"
                }`}>
                {label}
                <span className="ml-1 opacity-75">({filterCounts[id] ?? 0})</span>
              </button>
            ))}
          </div>
          {overview?.meta && (
            <>
              <MetaPill label="Shown patients" value={filteredOverview?.nodes.filter((n) => n.kind === "patient").length} />
              <MetaPill label="Free donors shown" value={filteredOverview?.nodes.filter((n) => n.kind === "donor").length} />
              <MetaPill label="Free donors total" value={overview.meta.totalFreeDonors} />
            </>
          )}
        </div>

        <GraphLegend />
        <div className="mt-3">
          {loadingOverview && !overview ? (
            <div className="h-[420px] grid place-items-center text-muted text-sm">Building blood graph…</div>
          ) : (
            <GraphNetwork
              data={filteredOverview}
              layout="force"
              height="460px"
              highlightNodeId={highlightPatientId}
              onNodeClick={handleNodeClick}
            />
          )}
        </div>
      </section>

      <section className="card p-5">
        <div className="flex flex-wrap items-start justify-between gap-3 mb-3">
          <div>
            <h2 className="font-head font-semibold text-ink">{focusTitle}</h2>
            <p className="text-xs text-muted mt-0.5 max-w-2xl">{focusSub}</p>
          </div>
          {(selectedPatient || focusGraph) && (
            <button onClick={clearFocus}
              className="px-3 py-1.5 rounded-lg border border-line text-xs font-semibold text-muted hover:text-ink">
              Clear selection
            </button>
          )}
        </div>

        {!focusGraph && !loadingFocus && (
          <div className="rounded-xl border border-dashed border-line bg-canvas/50 py-12 text-center text-sm text-muted">
            Click a <strong className="text-ink">patient star</strong> in the open pool above,
            or use <strong className="text-ink">View graph</strong> on an under-strength bridge in Operations.
          </div>
        )}

        {loadingFocus && (
          <div className="h-[380px] grid place-items-center text-muted text-sm">Loading patient view…</div>
        )}

        {focusGraph && !focusGraph.error && (
          <>
            <div className="flex flex-wrap gap-2 mb-3">
              <MetaPill label="Patient" value={focusGraph.patientName || selectedPatient?.label} />
              <MetaPill label="Group" value={focusGraph.bloodGroup} />
              {focusKind === "bridge" ? (
                <>
                  <MetaPill label="Active" value={focusGraph.active} />
                  <MetaPill label="Buffer" value={focusGraph.buffer} />
                  <MetaPill label="Vacant" value={focusGraph.vacant} />
                </>
              ) : (
                <MetaPill label="Candidates" value={focusGraph.candidateCount} />
              )}
            </div>
            <GraphLegend compact />
            <div className="mt-3">
              <GraphNetwork data={focusGraph} layout="fixed" height="400px" disableZoom />
            </div>
          </>
        )}
      </section>
    </div>
  );
}
