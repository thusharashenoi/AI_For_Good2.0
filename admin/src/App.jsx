import React, { useEffect, useMemo, useState } from "react";
import { api, shortId } from "./api.js";
import BloodGraphTab from "./BloodGraphTab.jsx";
import CalendarTab from "./CalendarTab.jsx";
import EmergencyTab from "./EmergencyTab.jsx";
import ErrorModal, { formatApiError } from "./ErrorModal.jsx";
import bwLongLogo from "./assets/bw-long-logo.png";

// Blood-group colours tuned for a light background.
const GROUP_COLORS = {
  "O-": "#e11d48", "O+": "#fb7185", "A-": "#2563eb", "A+": "#60a5fa",
  "B-": "#d97706", "B+": "#f59e0b", "AB-": "#7c3aed", "AB+": "#a78bfa",
};

function Stat({ label, value, accent }) {
  return (
    <div className="card p-5">
      <div className="text-muted text-xs font-semibold uppercase tracking-wider">{label}</div>
      <div className="text-3xl font-head font-bold mt-1" style={{ color: accent || "#141414" }}>
        {value ?? "—"}
      </div>
    </div>
  );
}

function GroupTag({ g }) {
  const c = GROUP_COLORS[g] || "#8a8a8a";
  return (
    <span className="px-2 py-0.5 rounded-md text-xs font-bold"
      style={{ background: c + "1a", color: c }}>
      {g || "?"}
    </span>
  );
}

function fillTierColor(active) {
  if (active < 6) return "#ef4444";
  if (active <= 8) return "#eab308";
  return "#10b981";
}

function FillBar({ active, buffer, vacant, target }) {
  const pct = (n) => `${(100 * n) / target}%`;
  const activeColor = fillTierColor(active);
  return (
    <div className="flex h-2.5 w-40 rounded-full overflow-hidden bg-line">
      <div style={{ width: pct(active), background: activeColor }} title={`${active} active`} />
      <div style={{ width: pct(buffer), background: "#f59e0b" }} title={`${buffer} buffer`} />
      <div style={{ width: pct(vacant), background: "#e9e9e9" }} title={`${vacant} vacant`} />
    </div>
  );
}

function BridgeDrawer({ patient, onClose }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [broadcast, setBroadcast] = useState(null);
  const [broadcasting, setBroadcasting] = useState(false);

  useEffect(() => {
    if (!patient) return;
    setData(null); setErr(null); setBroadcast(null);
    if (patient.candidatePreview?.candidates?.length) {
      setData({
        candidates: patient.candidatePreview.candidates,
        patientId: patient.patientId,
        patientName: patient.name,
        count: patient.candidatePreview.candidates.length,
      });
      return;
    }
    api.candidates(patient.patientId, 12).then(setData).catch((e) => setErr(formatApiError(e)));
  }, [patient]);

  async function handleBroadcast() {
    if (!patient?.bridgeId) return;
    setBroadcasting(true); setErr(null);
    try {
      const res = await api.broadcastBridge(patient.bridgeId);
      setBroadcast(res);
      if (res.ok && !res.voiceEscalationScheduled) {
        const detail = res.voiceEscalationError || "engagement server may be down on :4000";
        setErr(`WhatsApp was sent, but the follow-up voice call was not scheduled (${detail}).`);
        return;
      }
      if (res.ok && res.voiceEscalationScheduled) {
        const bridgeId = patient.bridgeId;
        const delaySec = res.voiceEscalationDelaySeconds ?? res.callDelaySec ?? 7;

        const pollVoiceStatus = async (attempt = 0) => {
          try {
            const status = await api.broadcastOutreachStatus(bridgeId);
            const result = status.escalationCallResult || {};
            const placed = (
              status.voiceOutreachPlaced
              || status.escalationCallPlaced
              || status.voiceCallId
              || result.ok
              || result.voicePlaced
            );
            if (placed) return;
            if (result.ok === false && result.reason) {
              const hint = result.reason === "missing_context"
                ? "Engagement server was not using live DynamoDB — restart: cd engagement && bash scripts/run_server.sh"
                : result.reason;
              setErr(`WhatsApp was sent, but the voice call failed (${hint}).`);
              return;
            }
            if (attempt < 10) {
              window.setTimeout(() => pollVoiceStatus(attempt + 1), 3000);
              return;
            }
            setErr("WhatsApp was sent, but no voice call confirmation yet. If your phone rang, you can ignore this.");
          } catch {
            /* ignore poll errors */
          }
        };

        window.setTimeout(() => pollVoiceStatus(0), (delaySec + 5) * 1000);
      }
    } catch (e) {
      setErr(formatApiError(e));
    } finally {
      setBroadcasting(false);
    }
  }

  if (!patient) return null;
  return (
    <>
      {err && (
        <ErrorModal
          message={err}
          title={err.startsWith("WhatsApp was sent") ? "Partial broadcast failure" : "Bridge error"}
          onDismiss={() => setErr(null)}
        />
      )}
      <div className="fixed inset-0 z-50 flex justify-end bg-ink/30 backdrop-blur-sm" onClick={onClose}>
      <div className="w-full max-w-md h-full bg-surface border-l border-line p-6 overflow-y-auto shadow-2xl"
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-lg font-head font-semibold text-ink">
            {patient.formed ? "Bridge formed" : "Proposed bridge"}
          </h3>
          <div className="flex items-center gap-2">
            {patient.formed && patient.bridgeId && (
              <button onClick={handleBroadcast} disabled={broadcasting || broadcast?.ok}
                className="px-3 py-1.5 rounded-lg bg-brand hover:bg-brand-dark disabled:opacity-50 text-white text-xs font-semibold transition-colors whitespace-nowrap">
                {broadcasting ? "Sending…" : broadcast?.ok ? "Broadcast sent" : "Broadcast Message"}
              </button>
            )}
            <button onClick={onClose} className="text-muted hover:text-ink text-lg">✕</button>
          </div>
        </div>
        <div className="mt-1 text-sm text-muted">
          {patient.name || shortId(patient.patientId)} · <GroupTag g={patient.bloodGroup} />
          {patient.bridgeId && (
            <span className="block font-mono text-[11px] mt-0.5">{patient.bridgeId}</span>
          )}
        </div>
        {broadcast?.ok && (
          <div className="mt-3 rounded-xl px-3 py-2 bg-success/10 border border-success/20 text-xs text-success-dark">
            WhatsApp sent from {broadcast.senderPhone || "Blood Warriors bot"} to {broadcast.demoPhone}
            ({broadcast.donorsTargeted} ranked donors).
            If no reply in {broadcast.callDelaySec}s, a voice call will follow to the same number.
          </div>
        )}
        {!data && !err && (
          <div className="mt-6 text-muted">
            {patient.forming ? "Forming bridge…" : "Ranking donors…"}
          </div>
        )}
        {data && (
          <ol className="mt-4 space-y-2">
            {data.candidates.map((c, i) => (
              <li key={c.donorId}
                className="rounded-xl px-3 py-2 bg-canvas border border-line">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2.5">
                    <span className="text-muted w-5 text-right text-sm">{i + 1}</span>
                    <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-md ${c.slotTypeHint === "active" ? "bg-success/10 text-success-dark" : "bg-warn/10 text-warn"}`}>
                      {c.slotTypeHint}
                    </span>
                    <span className="text-sm text-ink font-medium">{c.name || shortId(c.donorId)}</span>
                    <GroupTag g={c.group} />
                  </div>
                  <span className="font-bold text-ink">
                    {c.score != null ? Number(c.score).toFixed(2) : "—"}
                  </span>
                </div>
                <div className="flex items-center gap-4 mt-1 pl-7 text-xs text-muted">
                  <span>📍 {c.city ? `${c.city} · ` : ""}{c.distanceKm != null ? `${c.distanceKm} km` : "—"}</span>
                  <span>🤝 show-up {Math.round((c.showRate ?? 0) * 100)}%</span>
                  <span>💪 willing {Math.round((c.willingness ?? 0) * 100)}%</span>
                </div>
              </li>
            ))}
          </ol>
        )}
        <p className="mt-4 text-xs text-muted leading-relaxed">
          Ranked after gating on compatibility, 90-day eligibility, and patient-specific
          availability. Top 6 → active rotation; backups follow the no-show policy.
        </p>
      </div>
    </div>
    </>
  );
}

function Section({ title, sub, children, right }) {
  return (
    <div className="card p-5">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h2 className="font-head font-semibold text-ink">{title}</h2>
          {sub && <p className="text-xs text-muted mt-0.5">{sub}</p>}
        </div>
        {right}
      </div>
      {children}
    </div>
  );
}

const thCls = "text-muted text-[11px] font-semibold uppercase tracking-wide";

export default function App() {
  const [stats, setStats] = useState(null);
  const [bridges, setBridges] = useState(null);
  const [unbridged, setUnbridged] = useState(null);
  const [atRisk, setAtRisk] = useState(null);
  const [requests, setRequests] = useState(null);
  const [appointments, setAppointments] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState(null);
  const [formingPatientId, setFormingPatientId] = useState(null);
  const [tab, setTab] = useState("ops");
  const [graphBridgeId, setGraphBridgeId] = useState(null);
  const [deletingRequestId, setDeletingRequestId] = useState(null);
  const [deletingAppointmentId, setDeletingAppointmentId] = useState(null);

  function openBridgeGraph(bridgeId) {
    setGraphBridgeId(bridgeId);
    setTab("graph");
  }

  async function refresh() {
    setLoading(true); setError(null);
    try {
      const [d, req] = await Promise.all([api.dashboard(12), api.requests(10)]);
      setStats(d.stats);
      setBridges(d.bridges);
      setUnbridged(d.unbridged);
      setAtRisk(d.atRisk);
      setRequests(req);
      setAppointments(d.appointments);
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setLoading(false);
    }
  }

  async function handleDeleteRequest(requestId) {
    if (deletingRequestId) return;
    setDeletingRequestId(requestId);
    setError(null);
    try {
      await api.deleteRequest(requestId);
      await refresh();
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setDeletingRequestId(null);
    }
  }

  async function handleDeleteAppointment(appointmentId) {
    if (deletingAppointmentId) return;
    setDeletingAppointmentId(appointmentId);
    setError(null);
    try {
      await api.deleteAppointment(appointmentId);
      await refresh();
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setDeletingAppointmentId(null);
    }
  }

  async function handleFormBridge(p) {
    if (formingPatientId) return;
    setError(null);
    setFormingPatientId(p.patientId);
    setSelected({ ...p, forming: true, formed: false, candidatePreview: null });
    try {
      const res = await api.formBridge(p.patientId);
      if (!res.ok) {
        setSelected(null);
        setError(`No bridge formed: ${res.reason || "no candidates"} (pool ${res.coverage ?? 0})`);
        return;
      }
      setSelected({
        ...p,
        bridgeId: res.bridgeId,
        formed: true,
        forming: false,
        candidatePreview: res,
      });
      refresh();
    } catch (e) {
      setSelected(null);
      setError(formatApiError(e));
    } finally {
      setFormingPatientId(null);
    }
  }
  useEffect(() => { refresh(); }, []);

  const weakBridges = useMemo(
    () => (bridges?.bridges || [])
      .filter((b) => (b.fill ?? 0) < (b.target ?? 10))
      .sort((a, b) => (a.fill ?? 0) - (b.fill ?? 0) || (b.vacant ?? 0) - (a.vacant ?? 0))
      .slice(0, 12),
    [bridges]
  );

  return (
    <div className="min-h-full text-body">
      <header className="border-b border-line bg-surface/70 backdrop-blur sticky top-0 z-40">
        <div className="px-8 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3 min-w-0">
            <img
              src={bwLongLogo}
              alt="Blood Warriors"
              className="h-9 w-auto max-w-[min(220px,42vw)] object-contain object-left shrink-0"
            />
          </div>
          <div className="flex items-center gap-3">
            <div className="flex rounded-xl border border-line bg-canvas p-1">
              {[
                ["ops", "Operations"],
                ["calendar", "Calendar"],
                ["graph", "Blood Graph"],
                ["emergency", "Emergency"],
              ].map(([id, label]) => (
                <button key={id} onClick={() => setTab(id)}
                  className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
                    tab === id ? "bg-brand text-white shadow-sm" : "text-muted hover:text-ink"
                  }`}>
                  {label}
                </button>
              ))}
            </div>
            <button onClick={refresh}
            className="px-4 py-2 rounded-xl bg-brand hover:bg-brand-dark text-white text-sm font-semibold shadow-card transition-colors">
            {loading ? "Refreshing…" : "Refresh"}
          </button>
          </div>
        </div>
      </header>

      <main className="px-8 py-6 pb-16 space-y-6 max-w-[1400px] mx-auto">
        <ErrorModal message={error} onDismiss={() => setError(null)} />

        {tab === "graph" ? (
          <BloodGraphTab
            selectedBridgeId={graphBridgeId}
            onSelectBridge={setGraphBridgeId}
            onClearBridge={() => setGraphBridgeId(null)}
          />
        ) : tab === "calendar" ? (
          <CalendarTab />
        ) : tab === "emergency" ? (
          <EmergencyTab />
        ) : (
        <>
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
          <Stat label="Donors" value={stats?.donors} />
          <Stat label="Eligible now" value={stats?.eligibleDonors} accent="#059669" />
          <Stat label="Patients" value={stats?.patients} />
          <Stat label="Unbridged" value={stats?.unbridgedPatients} accent="#f59e0b" />
          <Stat label="Bridges" value={stats?.bridges} />
          <Stat label="Vacant slots" value={stats?.vacantSlots} accent="#f14164" />
        </div>

        <div className="grid lg:grid-cols-2 gap-6">
          <Section title="Unbridged patients"
            sub="No committed bridge yet — form a 6+4 plan or broadcast to donors once planned.">
            <table className="w-full text-sm">
              <thead>
                <tr className={thCls}><th className="text-left py-2 font-semibold">Patient</th><th>Group</th>
                  <th className="text-right">Donor pool</th><th className="text-right">Top score</th><th></th></tr>
              </thead>
              <tbody>
                {(unbridged?.patients || []).map((p) => (
                  <tr key={p.patientId} className="border-t border-line">
                    <td className="py-2.5 text-ink font-medium">{p.name || shortId(p.patientId)}
                      {p.city && <span className="text-muted font-normal text-xs"> · {p.city}</span>}</td>
                    <td className="text-center"><GroupTag g={p.bloodGroup} /></td>
                    <td className="text-right">{p.candidatePool}</td>
                    <td className="text-right">{p.topScore?.toFixed(2) ?? "—"}</td>
                    <td className="text-right">
                      <button onClick={() => handleFormBridge(p)}
                        disabled={formingPatientId === p.patientId}
                        className="px-3 py-1 rounded-lg bg-brand hover:bg-brand-dark disabled:opacity-60 disabled:cursor-wait text-white text-xs font-semibold transition-colors">
                        {formingPatientId === p.patientId ? "Forming…" : "Form bridge"}
                      </button>
                    </td>
                  </tr>
                ))}
                {unbridged && unbridged.patients.length === 0 && (
                  <tr><td colSpan="5" className="py-4 text-center text-muted">All patients bridged 🎉</td></tr>
                )}
              </tbody>
            </table>
          </Section>

          <Section title="At-risk patients"
            sub="Thinnest eligible + compatible donor pools — watch these first.">
            <table className="w-full text-sm">
              <thead>
                <tr className={thCls}><th className="text-left py-2 font-semibold">Patient</th><th>Group</th>
                  <th className="text-right">Eligible donors</th><th className="text-center">Bridged</th></tr>
              </thead>
              <tbody>
                {(atRisk?.patients || []).map((p) => (
                  <tr key={p.patientId} className="border-t border-line">
                    <td className="py-2.5 text-ink font-medium">{p.name || shortId(p.patientId)}</td>
                    <td className="text-center"><GroupTag g={p.bloodGroup} /></td>
                    <td className="text-right">
                      <span className={p.eligibleCompatibleDonors < 50 ? "text-danger font-bold" : ""}>
                        {p.eligibleCompatibleDonors}
                      </span>
                    </td>
                    <td className="text-center">{p.bridged ? <span className="text-success-dark">✓</span> : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Section>
        </div>

        <Section title="Open blood requests (WhatsApp / portal)"
          sub="Live requests from patient onboarding — donors registered via the bot appear in matching below.">
          <table className="w-full text-sm">
            <thead>
              <tr className={thCls}>
                <th className="text-left py-2 font-semibold">Patient</th>
                <th>Group</th>
                <th className="text-left">Hospital</th>
                <th className="text-right">Required by</th>
                <th className="text-center">Urgency</th>
              </tr>
            </thead>
            <tbody>
              {(requests?.requests || []).map((r) => (
                <tr key={r.requestId} className="border-t border-line group">
                  <td className="py-2.5 text-ink font-medium">{r.patientName || shortId(r.patientId)}</td>
                  <td className="text-center"><GroupTag g={r.bloodGroup} /></td>
                  <td className="text-muted">{r.hospital}{r.city ? ` · ${r.city}` : ""}</td>
                  <td className="text-right text-muted">{r.requiredBy || "—"}</td>
                  <td className="text-center text-xs uppercase">{r.urgencyLevel || "—"}</td>
                  <td className="py-2.5 pl-2 text-right align-middle w-12">
                    <button
                      type="button"
                      onClick={() => handleDeleteRequest(r.requestId)}
                      disabled={deletingRequestId === r.requestId}
                      className="opacity-0 group-hover:opacity-100 focus:opacity-100 text-[11px] text-muted/35 hover:text-danger/70 transition-all disabled:opacity-30"
                      title="Delete request"
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
              {requests && requests.requests.length === 0 && (
                <tr><td colSpan="6" className="py-4 text-center text-muted">No open requests</td></tr>
              )}
            </tbody>
          </table>
        </Section>

        <Section title="Donation appointments (WhatsApp / call)"
          sub="Booked after bridge mobilization — health check → auto-scheduled slot.">
          <table className="w-full text-sm">
            <thead>
              <tr className={thCls}>
                <th className="text-left py-2 font-semibold">Donor</th>
                <th className="text-left">Patient</th>
                <th>Group</th>
                <th className="text-left">Hospital</th>
                <th className="text-right">When</th>
                <th className="text-center">Channel</th>
                <th className="text-left">Bridge</th>
              </tr>
            </thead>
            <tbody>
              {(appointments?.appointments || []).map((a) => (
                <tr key={a.appointmentId} className="border-t border-line group">
                  <td className="py-2.5 text-ink font-medium">
                    {a.donorName || shortId(a.donorPhone)}
                    {a.donorPhone && (
                      <span className="block text-muted font-normal text-[11px]">{a.donorPhone}</span>
                    )}
                  </td>
                  <td className="text-ink">{a.patientName || "—"}</td>
                  <td className="text-center"><GroupTag g={a.bloodGroup} /></td>
                  <td className="text-muted">{a.hospital}{a.city ? ` · ${a.city}` : ""}</td>
                  <td className="text-right text-muted whitespace-nowrap">
                    {a.appointmentDate || "—"}
                    {a.appointmentTime ? ` · ${a.appointmentTime}` : ""}
                  </td>
                  <td className="text-center text-xs uppercase">{a.channel || "—"}</td>
                  <td className="text-muted font-mono text-[11px]">{a.bridgeId || "—"}</td>
                  <td className="py-2.5 pl-2 text-right align-middle w-12">
                    <button
                      type="button"
                      onClick={() => handleDeleteAppointment(a.appointmentId)}
                      disabled={deletingAppointmentId === a.appointmentId}
                      className="opacity-0 group-hover:opacity-100 focus:opacity-100 text-[11px] text-muted/35 hover:text-danger/70 transition-all disabled:opacity-30"
                      title="Delete appointment"
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
              {appointments && appointments.appointments.length === 0 && (
                <tr><td colSpan="8" className="py-4 text-center text-muted">
                  No appointments yet — complete a voice or WhatsApp booking, then refresh.
                </td></tr>
              )}
            </tbody>
          </table>
        </Section>

        <Section title="Bridge health"
          sub="Only patients with at least one agreed donor. Active bar: red (&lt;6) · yellow (6–8) · green (9–10). Target = 10 donors.">
          <table className="w-full text-sm">
            <thead>
              <tr className={thCls}><th className="text-left py-2 font-semibold">Bridge</th><th>Group</th><th>Status</th>
                <th className="text-right">Active</th><th className="text-right">Buffer</th>
                <th className="text-right">Vacant</th><th className="text-left pl-6">Fill</th><th></th></tr>
            </thead>
            <tbody>
              {weakBridges.map((b) => (
                <tr key={b.bridgeId} className="border-t border-line">
                  <td className="py-2.5 text-ink font-medium">{b.patientName || b.bridgeId}
                    <span className="block text-muted font-mono font-normal text-[11px]">{b.bridgeId}</span></td>
                  <td className="text-center"><GroupTag g={b.bloodGroup} /></td>
                  <td className="text-center text-xs text-muted">{b.status}</td>
                  <td className="text-right text-success-dark font-semibold">{b.active}</td>
                  <td className="text-right text-warn font-semibold">{b.buffer}</td>
                  <td className="text-right text-muted">{b.vacant}</td>
                  <td className="pl-6"><FillBar active={b.active} buffer={b.buffer} vacant={b.vacant} target={b.target} /></td>
                  <td className="text-right">
                    {b.active < 10 && (
                      <button onClick={() => openBridgeGraph(b.bridgeId)}
                        className="px-3 py-1 rounded-lg border border-brand/40 bg-brand-soft hover:bg-brand/10 text-brand text-xs font-semibold transition-colors whitespace-nowrap">
                        View graph
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {weakBridges.length === 0 && (
                <tr><td colSpan="8" className="py-4 text-center text-muted">Every bridge is at full strength.</td></tr>
              )}
            </tbody>
          </table>
        </Section>

        <footer className="text-center text-xs text-muted pt-2">
          Blood Warriors Bridge Intelligence · coordinator extension ·{" "}
          <a href="https://www.bloodwarriors.in/leaderboard" target="_blank" rel="noreferrer"
            className="text-brand hover:underline">
            bloodwarriors.in
          </a>
        </footer>
        </>
        )}
      </main>

      <BridgeDrawer patient={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
