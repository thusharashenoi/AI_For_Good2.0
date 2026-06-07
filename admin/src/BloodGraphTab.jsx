import React, { useCallback, useEffect, useMemo, useState } from "react";
import { api, shortId } from "./api.js";
import ErrorModal, { formatApiError } from "./ErrorModal.jsx";
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

const thCls = "text-muted text-[11px] font-semibold uppercase tracking-wide";

const GROUP_COLORS = {
  "O-": "#e11d48", "O+": "#fb7185", "A-": "#2563eb", "A+": "#60a5fa",
  "B-": "#d97706", "B+": "#f59e0b", "AB-": "#7c3aed", "AB+": "#a78bfa",
};

function GroupTag({ g }) {
  const c = GROUP_COLORS[g] || "#8a8a8a";
  return (
    <span className="px-2 py-0.5 rounded-md text-xs font-bold"
      style={{ background: c + "1a", color: c }}>
      {g || "?"}
    </span>
  );
}

function roleLabel(role, slotTypeHint) {
  if (role === "active") return "Active";
  if (role === "buffer") return "Buffer";
  if (role === "bridge") return "Member";
  if (role === "candidate") return slotTypeHint ? `Tentative · ${slotTypeHint}` : "Tentative";
  return role || "—";
}

function buildDonorRows(focusGraph) {
  const edgeByDonor = new Map();
  for (const e of focusGraph?.edges || []) {
    edgeByDonor.set(e.to, e);
  }
  const members = [];
  const candidates = [];
  for (const n of focusGraph?.nodes || []) {
    if (n.kind !== "donor") continue;
    const edge = edgeByDonor.get(n.id);
    const row = {
      id: n.id,
      name: n.label,
      group: n.group,
      city: n.city,
      role: n.role,
      slotId: n.slotId,
      backupFor: n.backupFor,
      slotTypeHint: n.slotTypeHint,
      showRate: n.showRate,
      score: edge?.score,
    };
    if (n.role === "candidate") candidates.push(row);
    else members.push(row);
  }
  candidates.sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
  members.sort((a, b) => String(a.slotId || "").localeCompare(String(b.slotId || "")));
  return { members, candidates };
}

function DonorTable({ title, rows, emptyText }) {
  return (
    <div>
      <h4 className="font-head font-semibold text-ink text-sm mb-2">{title}</h4>
      {rows.length === 0 ? (
        <p className="text-xs text-muted italic">{emptyText}</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-line">
          <table className="w-full text-xs min-w-[280px]">
            <thead>
              <tr className={thCls}>
                <th className="text-left py-2 px-2">Donor</th>
                <th>Group</th>
                <th className="text-left">Role</th>
                <th className="text-right py-2 px-2">Match</th>
                <th className="text-right py-2 pl-2 pr-3">Show-up</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} className="border-t border-line">
                  <td className="py-2 px-2 text-ink font-medium">
                    {row.name}
                    {row.city && <span className="block text-muted font-normal">{row.city}</span>}
                    {row.slotId && (
                      <span className="block text-muted font-mono text-[10px]">
                        Slot {row.slotId}{row.backupFor ? ` · backup for ${row.backupFor}` : ""}
                      </span>
                    )}
                  </td>
                  <td className="text-center"><GroupTag g={row.group} /></td>
                  <td className="text-muted capitalize">{roleLabel(row.role, row.slotTypeHint)}</td>
                  <td className="text-right text-ink font-semibold px-2">
                    {row.score != null ? `${Math.round(row.score * 100)}%` : "—"}
                  </td>
                  <td className="text-right text-muted pl-2 pr-3">
                    {row.showRate != null ? `${Math.round(row.showRate * 100)}%` : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function FocusDetailPanel({ focusGraph, focusKind }) {
  const { members, candidates } = useMemo(() => buildDonorRows(focusGraph), [focusGraph]);
  const patientNode = focusGraph?.nodes?.find((n) => n.kind === "patient");

  const details = [
    ["Name", focusGraph?.patientName || patientNode?.label],
    ["Blood group", focusGraph?.bloodGroup || patientNode?.group],
    ["City", patientNode?.city],
    focusKind === "bridge"
      ? ["Bridge ID", focusGraph?.bridgeId]
      : ["Patient ID", shortId(focusGraph?.patientId)],
    focusKind === "bridge"
      ? ["Bridge fill", `${(focusGraph?.active ?? 0) + (focusGraph?.buffer ?? 0)}/${focusGraph?.target ?? 10}`]
      : ["Status", focusGraph?.reason === "unbridged" ? "Unbridged" : "Patient focus"],
  ];

  if (focusKind === "bridge") {
    details.push(
      ["Active slots", focusGraph?.active],
      ["Buffer slots", focusGraph?.buffer],
      ["Vacant slots", focusGraph?.vacant],
    );
  } else {
    details.push(["Tentative donors", candidates.length]);
  }

  return (
    <div className="space-y-4 max-h-[520px] overflow-y-auto pr-4">
      <div>
        <h4 className="font-head font-semibold text-ink text-sm mb-2">Patient details</h4>
        <div className="rounded-lg border border-line overflow-hidden">
          <table className="w-full text-xs">
            <tbody>
              {details.map(([label, value]) => (
                <tr key={label} className="border-t border-line first:border-t-0">
                  <td className="py-2 pl-3 pr-2 text-muted w-2/5">{label}</td>
                  <td className="py-2 pl-3 pr-4 text-ink font-medium break-all">
                    {label === "Blood group" && value ? <GroupTag g={value} /> : (value ?? "—")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <DonorTable
        title={focusKind === "bridge" ? "Bridge donors" : "Existing bridge donors"}
        rows={members}
        emptyText={focusKind === "bridge"
          ? "No confirmed bridge members yet."
          : "No bridge formed — patient is unbridged."}
      />

      <DonorTable
        title="Top tentative donors"
        rows={candidates}
        emptyText="No ranked candidates in the free pool."
      />
    </div>
  );
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
      setError(formatApiError(e));
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
      setError(formatApiError(e));
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
      <ErrorModal message={error} onDismiss={() => setError(null)} />

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
          <div className="grid lg:grid-cols-5 gap-5 items-start">
            <div className="lg:col-span-3 space-y-3">
              <GraphLegend compact />
              <GraphNetwork data={focusGraph} layout="fixed" height="520px" disableZoom />
            </div>
            <div className="lg:col-span-2 rounded-xl border border-line bg-canvas p-4 lg:sticky lg:top-4">
              <FocusDetailPanel focusGraph={focusGraph} focusKind={focusKind} />
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
