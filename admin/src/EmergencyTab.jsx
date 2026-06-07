import React, { useEffect, useState } from "react";
import { api } from "./api.js";
import ErrorModal, { formatApiError } from "./ErrorModal.jsx";

const BLOOD_GROUPS = ["O+", "O-", "A+", "A-", "B+", "B-", "AB+", "AB-"];

function GroupTag({ g }) {
  const colors = {
    "O-": "#e11d48", "O+": "#fb7185", "A-": "#2563eb", "A+": "#60a5fa",
    "B-": "#d97706", "B+": "#f59e0b", "AB-": "#7c3aed", "AB+": "#a78bfa",
  };
  const c = colors[g] || "#8a8a8a";
  return (
    <span className="px-2 py-0.5 rounded-md text-xs font-bold"
      style={{ background: c + "1a", color: c }}>{g}</span>
  );
}

export default function EmergencyTab() {
  const [cities, setCities] = useState(["Hyderabad"]);
  const [form, setForm] = useState({
    patient_name: "",
    blood_group: "O+",
    city: "Hyderabad",
  });
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.emergencyCities().then((d) => setCities(d.cities || ["Hyderabad"])).catch(() => {});
  }, []);

  async function onSubmit(e) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      setResult(await api.emergencyMatch({ ...form, limit: 15 }));
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setLoading(false);
    }
  }

  const thCls = "text-muted text-[11px] font-semibold uppercase tracking-wide";

  return (
    <div className="space-y-6">
      <ErrorModal message={error} onDismiss={() => setError(null)} />

      <section className="card p-5">
        <h2 className="font-head font-semibold text-ink">Emergency request</h2>
        <p className="text-xs text-muted mt-1 max-w-2xl">
          Match a patient <strong className="text-ink">not in the Blood Warriors registry</strong>.
          Donors are ranked after compatibility, eligibility, availability, proximity, show-up rate,
          and willingness. Bridge members include a suggested backfill for their slot.
        </p>

        <form onSubmit={onSubmit} className="mt-4 grid sm:grid-cols-2 lg:grid-cols-4 gap-3">
          <label className="block">
            <span className="text-xs font-semibold text-muted uppercase tracking-wide">Patient name</span>
            <input required value={form.patient_name}
              onChange={(e) => setForm({ ...form, patient_name: e.target.value })}
              className="mt-1 w-full rounded-xl border border-line bg-canvas px-3 py-2 text-sm text-ink"
              placeholder="e.g. Rahul Kumar" />
          </label>
          <label className="block">
            <span className="text-xs font-semibold text-muted uppercase tracking-wide">Blood group</span>
            <select value={form.blood_group}
              onChange={(e) => setForm({ ...form, blood_group: e.target.value })}
              className="mt-1 w-full rounded-xl border border-line bg-canvas px-3 py-2 text-sm text-ink">
              {BLOOD_GROUPS.map((g) => <option key={g} value={g}>{g}</option>)}
            </select>
          </label>
          <label className="block">
            <span className="text-xs font-semibold text-muted uppercase tracking-wide">City (Telangana)</span>
            <select value={form.city}
              onChange={(e) => setForm({ ...form, city: e.target.value })}
              className="mt-1 w-full rounded-xl border border-line bg-canvas px-3 py-2 text-sm text-ink">
              {cities.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </label>
          <div className="flex items-end">
            <button type="submit" disabled={loading}
              className="w-full px-4 py-2 rounded-xl bg-brand hover:bg-brand-dark text-white text-sm font-semibold disabled:opacity-50">
              {loading ? "Matching…" : "Find donors"}
            </button>
          </div>
        </form>
      </section>

      {result && (
        <section className="card p-5">
          <div className="flex flex-wrap items-center gap-3 mb-4">
            <h3 className="font-head font-semibold text-ink">{result.request.patientName}</h3>
            <GroupTag g={result.request.bloodGroup} />
            <span className="text-xs text-muted">{result.request.city}</span>
            <span className="text-xs text-muted">
              {result.count} shown · {result.poolSize} eligible pool
            </span>
          </div>

          <table className="w-full text-sm">
            <thead>
              <tr className={thCls}>
                <th className="text-left py-2">Donor</th>
                <th>Group</th>
                <th className="text-right">Score</th>
                <th className="text-right">km</th>
                <th className="text-right">Show-up</th>
                <th className="text-right">Willingness</th>
                <th className="text-left pl-4">Bridge / backfill</th>
              </tr>
            </thead>
            <tbody>
              {result.candidates.map((c, i) => (
                <tr key={c.donorId} className="border-t border-line align-top">
                  <td className="py-2.5">
                    <span className="text-muted text-xs mr-2">{i + 1}</span>
                    <span className="font-medium text-ink">{c.name || c.donorId.slice(0, 8)}</span>
                    {c.city && <span className="block text-xs text-muted pl-6">{c.city}</span>}
                  </td>
                  <td className="text-center"><GroupTag g={c.group} /></td>
                  <td className="text-right font-semibold">{c.score.toFixed(2)}</td>
                  <td className="text-right text-muted">{c.distanceKm ?? "—"}</td>
                  <td className="text-right">{Math.round(c.showRate * 100)}%</td>
                  <td className="text-right">{Math.round(c.willingness * 100)}%</td>
                  <td className="pl-4 py-2.5 text-xs">
                    {c.inBridge ? (
                      <div className="space-y-1">
                        <span className="inline-block px-2 py-0.5 rounded-md bg-warn/10 text-warn font-semibold">
                          In bridge · {c.bridgePatientName || "patient"}
                        </span>
                        {c.replacement ? (
                          <p className="text-muted leading-relaxed">
                            Backfill: <strong className="text-ink">{c.replacement.name}</strong>
                            {" "}(score {c.replacement.score.toFixed(2)})
                            {" "}→ slot {c.replacement.fillsSlot} for {c.replacement.bridgePatientName}
                          </p>
                        ) : (
                          <p className="text-danger">No backfill available — pick a free-pool donor instead</p>
                        )}
                      </div>
                    ) : (
                      <span className="text-success-dark font-medium">Free pool</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}
