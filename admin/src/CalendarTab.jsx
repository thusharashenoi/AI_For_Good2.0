import React, { useCallback, useEffect, useMemo, useState } from "react";
import { api, shortId } from "./api.js";
import ErrorModal, { formatApiError } from "./ErrorModal.jsx";

const GROUP_COLORS = {
  "O-": "#e11d48", "O+": "#fb7185", "A-": "#2563eb", "A+": "#60a5fa",
  "B-": "#d97706", "B+": "#f59e0b", "AB-": "#7c3aed", "AB+": "#a78bfa",
};

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function groupColor(g) {
  return GROUP_COLORS[g] || "#8a8a8a";
}

function groupStyle(g, alpha = "22") {
  const c = groupColor(g);
  return { borderColor: c, backgroundColor: c + alpha, color: c };
}

function GroupTag({ g }) {
  const c = groupColor(g);
  return (
    <span className="px-2 py-0.5 rounded-md text-xs font-bold"
      style={{ background: c + "1a", color: c }}>
      {g || "?"}
    </span>
  );
}

function reliabilityColor(r) {
  if (r == null) return "#8a8a8a";
  if (r >= 0.85) return "#059669";
  if (r >= 0.7) return "#d97706";
  return "#ef4444";
}

function DonorChip({ donor, role, roleLabel }) {
  if (!donor) return null;
  const rel = donor.reliability;
  const relColor = reliabilityColor(rel);
  const bg = groupStyle(donor.bloodGroup, role === "backup" ? "12" : "20");
  const label = roleLabel || (role === "backup" ? "Backup" : role === "scheduled" ? "Scheduled donor" : "Primary");
  return (
    <div className="rounded-lg border px-3 py-2 text-xs" style={bg}>
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2 min-w-0">
          <span className="font-semibold text-ink truncate">{donor.name || shortId(donor.donorId)}</span>
          {donor.bloodGroup && <GroupTag g={donor.bloodGroup} />}
        </div>
        <span className="text-[10px] uppercase font-bold tracking-wide shrink-0"
          style={{ color: role === "backup" ? relColor : groupColor(donor.bloodGroup) }}>
          {label}
        </span>
      </div>
      <div className="mt-1 text-muted flex flex-wrap gap-x-3 gap-y-0.5">
        {rel != null && <span>Reliability {(rel * 100).toFixed(0)}%</span>}
        {donor.showRate != null && <span>Show-up {(donor.showRate * 100).toFixed(0)}%</span>}
        {donor.slotId && <span>Slot {donor.slotId}</span>}
        {donor.city && <span>{donor.city}</span>}
      </div>
      {donor.reason && (
        <p className="mt-1 text-[11px] text-muted leading-snug">{donor.reason}</p>
      )}
    </div>
  );
}

function cycleDonors(ev) {
  return ev?.assigned?.length ? ev.assigned : (ev?.coverage || []);
}

function uniqueDonorGroups(events) {
  const groups = new Set();
  for (const ev of events) {
    for (const row of cycleDonors(ev)) {
      if (row.primary?.bloodGroup) groups.add(row.primary.bloodGroup);
      if (row.backup?.bloodGroup) groups.add(row.backup.bloodGroup);
    }
  }
  return [...groups];
}

function primaryGroupFromEvents(events) {
  for (const ev of events || []) {
    for (const row of cycleDonors(ev)) {
      if (row.primary?.bloodGroup) return row.primary.bloodGroup;
    }
  }
  return null;
}

function BackupStatus({ row, threshold = 0.9 }) {
  const rel = row.primary?.reliability;
  const needs = row.needsBackup ?? (rel != null && rel < threshold);

  if (row.backup) {
    return <DonorChip donor={row.backup} role="backup" />;
  }
  if (needs || row.missingBackup) {
    return (
      <p className="text-[11px] text-warn pl-1 italic leading-snug">
        Backup required — reliability below {(threshold * 100).toFixed(0)}%
        {row.missingBackup ? " · no standby assigned yet" : ""}
      </p>
    );
  }
  return (
    <p className="text-[11px] text-muted pl-1 italic">
      No backup — trusted donor (≥{(threshold * 100).toFixed(0)}% reliability)
    </p>
  );
}

function formatWindowRange(ev) {
  if (!ev?.windowStart) return null;
  const end = ev.windowEnd || ev.transfusionDate;
  if (!end) return ev.windowStart;
  if (ev.windowStart === end) return ev.windowStart;
  return `${ev.windowStart} → ${end}`;
}

function MonthSection({ month, defaultOpen, selectedDate, onSelectDate }) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div className={`rounded-xl border overflow-hidden ${month.isCurrentMonth ? "border-brand/40" : "border-line"}`}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={`w-full flex items-center justify-between gap-3 px-4 py-3 text-left transition-colors ${
          month.isCurrentMonth ? "bg-brand-soft/40 hover:bg-brand-soft/60" : "bg-canvas hover:bg-canvas/80"
        }`}
      >
        <div className="flex items-center gap-2">
          <span className="font-head font-semibold text-ink">{month.label}</span>
          {month.isCurrentMonth && (
            <span className="text-[10px] uppercase font-bold text-brand px-2 py-0.5 rounded-md bg-brand/10">
              Current
            </span>
          )}
        </div>
        <span className="text-muted text-sm">{open ? "▾" : "▸"}</span>
      </button>

      {open && (
        <div className="px-4 pb-4 pt-2 border-t border-line bg-surface">
          <div className="grid grid-cols-7 gap-1 mb-1">
            {WEEKDAYS.map((d) => (
              <div key={d} className="text-center text-[10px] font-semibold text-muted uppercase py-1">{d}</div>
            ))}
          </div>
          <div className="space-y-1">
            {month.weeks.map((week, wi) => (
              <div key={wi} className="grid grid-cols-7 gap-1">
                {week.map((day) => {
                  if (!day.inMonth) {
                    return <div key={day.date} className="min-h-[4.5rem] rounded-lg bg-transparent" />;
                  }
                  const groups = uniqueDonorGroups(day.events);
                  const primaryGroup = primaryGroupFromEvents(day.events);
                  const tint = primaryGroup ? groupStyle(primaryGroup, "18") : null;
                  const selected = selectedDate === day.date;
                  return (
                    <button
                      key={day.date}
                      type="button"
                      onClick={() => onSelectDate(day.date, day.events)}
                      style={!selected && day.hasCoverage && tint
                        ? { borderLeftWidth: 3, borderLeftColor: tint.borderColor, backgroundColor: tint.backgroundColor }
                        : undefined}
                      className={`min-h-[4.5rem] rounded-lg border p-1.5 text-left transition-all ${
                        selected
                          ? "border-brand ring-2 ring-brand/20 bg-brand-soft/30"
                          : day.isToday
                            ? "border-brand/30 bg-brand-soft/20"
                            : day.hasCoverage
                              ? "border-line hover:border-brand/30"
                              : "border-line/60 bg-surface hover:bg-canvas"
                      }`}
                    >
                      <div className="flex items-start justify-between gap-1">
                        <span className={`text-xs font-semibold ${day.isToday ? "text-brand" : "text-ink"}`}>
                          {day.day}
                        </span>
                        {day.isTransfusionDay && (
                          <span className="text-[8px] font-bold uppercase text-brand leading-none">Tx</span>
                        )}
                      </div>
                      <div className="mt-1 flex flex-wrap gap-0.5">
                        {groups.slice(0, 4).map((g) => (
                          <span
                            key={g}
                            className="w-2 h-2 rounded-full shrink-0"
                            style={{ backgroundColor: groupColor(g) }}
                            title={g}
                          />
                        ))}
                      </div>
                      {day.hasCoverage && (
                        <p className="mt-1 text-[9px] text-muted leading-tight line-clamp-2">
                          {formatWindowRange(day.events[0])}
                        </p>
                      )}
                    </button>
                  );
                })}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function DayDetailPanel({ date, events, backupThreshold = 0.9 }) {
  if (!date || !events?.length) {
    return (
      <p className="text-sm text-muted italic py-4">
        Select a highlighted day to see the scheduled donor and cycle details.
      </p>
    );
  }

  const ev = events[0];
  const donors = cycleDonors(ev);
  const scheduledName = donors[0]?.primary?.name || shortId(donors[0]?.primary?.donorId);

  return (
    <div className="space-y-4">
      <div>
        <h4 className="font-head font-semibold text-ink">{date}</h4>
        {ev.isTransfusionDay ? (
          <p className="text-xs text-brand font-semibold mt-0.5">Donation day</p>
        ) : (
          <p className="text-xs text-muted mt-0.5">
            Prep window · donation on <span className="font-mono">{ev.transfusionDate}</span>
          </p>
        )}
      </div>

      <div className={`rounded-xl border p-4 ${ev.isCurrent ? "border-brand/40 bg-brand-soft/20" : "border-line bg-canvas"}`}>
        <div className="flex flex-wrap items-start justify-between gap-2 mb-3">
          <div>
            <span className="font-semibold text-ink text-sm">{ev.label}</span>
            {ev.isCurrent && (
              <span className="ml-2 text-[10px] uppercase font-bold text-brand">Current cycle</span>
            )}
          </div>
        </div>

        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-xs mb-4">
          <dt className="text-muted">Window</dt>
          <dd className="font-mono text-ink">{formatWindowRange(ev)}</dd>
          <dt className="text-muted">Transfusion</dt>
          <dd className="font-mono text-ink">{ev.transfusionDate || "—"}</dd>
          {(ev.unitsRequired || 1) > 1 && (
            <>
              <dt className="text-muted">Units</dt>
              <dd className="text-ink">{ev.unitsRequired} donors this cycle</dd>
            </>
          )}
          {donors.length === 1 && scheduledName && (
            <>
              <dt className="text-muted">Scheduled</dt>
              <dd className="text-ink font-semibold">{scheduledName}</dd>
            </>
          )}
        </dl>

        <div className="space-y-3">
          {donors.map((row) => (
            <div key={row.slotId} className="space-y-1.5">
              {(ev.unitsRequired || 1) > 1 && (
                <p className="text-[10px] uppercase font-bold text-muted tracking-wide">
                  Unit {row.unitNumber || row.slotId}
                </p>
              )}
              <DonorChip donor={row.primary} role="scheduled" />
              <BackupStatus row={row} threshold={backupThreshold} />
            </div>
          ))}
          {!donors.length && (
            <p className="text-sm text-muted italic">No donor assigned for this cycle.</p>
          )}
        </div>
      </div>
    </div>
  );
}

function CalendarModal({ patient, onClose }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [loading, setLoading] = useState(true);
  const [selectedDate, setSelectedDate] = useState(null);
  const [selectedEvents, setSelectedEvents] = useState([]);

  useEffect(() => {
    if (!patient?.patientId) return;
    setLoading(true);
    setErr(null);
    setData(null);
    setSelectedDate(null);
    setSelectedEvents([]);
    api.calendarPatient(patient.patientId)
      .then((res) => {
        setData(res);
        const current = (res.months || []).find((m) => m.isCurrentMonth);
        if (current) {
          for (const week of current.weeks) {
            for (const day of week) {
              if (day.isToday && day.hasCoverage) {
                setSelectedDate(day.date);
                setSelectedEvents(day.events);
                return;
              }
            }
          }
          for (const week of current.weeks) {
            for (const day of week) {
              if (day.isTransfusionDay) {
                setSelectedDate(day.date);
                setSelectedEvents(day.events);
                return;
              }
            }
          }
        }
      })
      .catch((e) => setErr(formatApiError(e)))
      .finally(() => setLoading(false));
  }, [patient]);

  const handleSelectDate = useCallback((date, events) => {
    setSelectedDate(date);
    setSelectedEvents(events || []);
  }, []);

  const legendGroups = useMemo(() => {
    const set = new Set();
    for (const m of data?.months || []) {
      for (const w of m.weeks) {
        for (const d of w) {
          uniqueDonorGroups(d.events).forEach((g) => set.add(g));
        }
      }
    }
    return [...set].sort();
  }, [data]);

  if (!patient) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-ink/30 backdrop-blur-sm p-4 overflow-y-auto"
      onClick={onClose}>
      <div className="w-full max-w-4xl my-8 bg-surface border border-line rounded-2xl shadow-2xl"
        onClick={(e) => e.stopPropagation()}>
        <div className="px-6 py-4 border-b border-line flex items-start justify-between gap-4">
          <div>
            <h3 className="text-lg font-head font-semibold text-ink">Transfusion calendar</h3>
            <p className="text-sm text-muted mt-0.5">
              {patient.patientName || shortId(patient.patientId)} · <GroupTag g={patient.bloodGroup} />
              {patient.city && <span> · {patient.city}</span>}
            </p>
            <p className="text-xs text-muted font-mono mt-1">{patient.bridgeId}</p>
          </div>
          <button onClick={onClose} className="text-muted hover:text-ink text-lg shrink-0">✕</button>
        </div>

        <div className="px-6 py-4">
          {loading && <p className="text-muted text-sm">Loading calendar…</p>}
          {err && (
            <ErrorModal message={err} onDismiss={() => setErr(null)} />
          )}
          {data && (
            <>
              <div className="flex flex-wrap gap-2 mb-4">
                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-canvas border border-line text-xs">
                  <span className="text-muted">Cadence</span>
                  <span className="font-semibold">{data.cadenceDays} days</span>
                </span>
                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-canvas border border-line text-xs">
                  <span className="text-muted">Next need</span>
                  <span className="font-semibold">{data.nextTransfusionDate || "—"}</span>
                </span>
                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-canvas border border-line text-xs">
                  <span className="text-muted">Active / buffer</span>
                  <span className="font-semibold">{data.activeDonors} / {data.bufferDonors}</span>
                </span>
              </div>

              <div className="flex flex-wrap gap-3 mb-4 text-[11px] items-center">
                <span className="text-muted">Donor blood group:</span>
                {legendGroups.map((g) => (
                  <span key={g} className="inline-flex items-center gap-1.5">
                    <span className="w-3 h-3 rounded-full" style={{ backgroundColor: groupColor(g) }} />
                    {g}
                  </span>
                ))}
                <span className="inline-flex items-center gap-1.5 ml-2">
                  <span className="text-[8px] font-bold uppercase text-brand border border-brand/40 px-1 rounded">Tx</span>
                  Transfusion day
                </span>
              </div>

              <div className="grid lg:grid-cols-5 gap-6">
                <div className="lg:col-span-3 space-y-3 max-h-[62vh] overflow-y-auto pr-1">
                  {(data.months || []).map((month) => (
                    <MonthSection
                      key={month.monthKey}
                      month={month}
                      defaultOpen={month.isCurrentMonth}
                      selectedDate={selectedDate}
                      onSelectDate={handleSelectDate}
                    />
                  ))}
                </div>
                <div className="lg:col-span-2 lg:sticky lg:top-0 self-start rounded-xl border border-line bg-canvas p-4 max-h-[62vh] overflow-y-auto">
                  <h4 className="font-head font-semibold text-ink text-sm mb-3">Coverage details</h4>
                  <DayDetailPanel
                    date={selectedDate}
                    events={selectedEvents}
                    backupThreshold={data.backupReliabilityThreshold ?? 0.9}
                  />
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

const thCls = "text-muted text-[11px] font-semibold uppercase tracking-wide";

export default function CalendarTab() {
  const [patients, setPatients] = useState([]);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(null);

  const load = useCallback(async (q) => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.calendarPatients(q);
      setPatients(res.patients || []);
    } catch (e) {
      setError(formatApiError(e));
      setPatients([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const t = setTimeout(() => setQuery(search.trim()), 250);
    return () => clearTimeout(t);
  }, [search]);

  useEffect(() => {
    load(query);
  }, [load, query]);

  const filteredCount = useMemo(() => patients.length, [patients]);

  return (
    <div className="space-y-6">
      <ErrorModal message={error} onDismiss={() => setError(null)} />

      <div className="card p-5">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-4">
          <div>
            <h2 className="font-head font-semibold text-ink">Bridged patient calendars</h2>
            <p className="text-xs text-muted mt-0.5">
              Month view with donor coverage windows — colours match donor blood group.
            </p>
          </div>
          <div className="relative w-full sm:w-72">
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search name, group, city, bridge…"
              className="w-full rounded-xl border border-line bg-canvas px-3 py-2 text-sm text-ink placeholder:text-muted focus:outline-none focus:ring-2 focus:ring-brand/30"
            />
          </div>
        </div>

        <p className="text-xs text-muted mb-3">
          {loading ? "Loading…" : `${filteredCount} bridged patient${filteredCount === 1 ? "" : "s"}`}
        </p>

        <div className="overflow-x-auto">
          <table className="w-full text-sm min-w-[640px]">
            <thead>
              <tr className={thCls}>
                <th className="text-left py-2 font-semibold">Patient</th>
                <th>Group</th>
                <th className="text-left">City</th>
                <th className="text-right">Next transfusion</th>
                <th className="text-right">Days</th>
                <th className="text-right">Active</th>
                <th className="text-right">Buffer</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {patients.map((p) => (
                <tr key={p.patientId} className="border-t border-line">
                  <td className="py-2.5 text-ink font-medium">
                    {p.patientName || shortId(p.patientId)}
                    <span className="block text-muted font-mono font-normal text-[11px]">{p.bridgeId}</span>
                  </td>
                  <td className="text-center"><GroupTag g={p.bloodGroup} /></td>
                  <td className="text-muted">{p.city || "—"}</td>
                  <td className="text-right text-muted whitespace-nowrap">{p.nextTransfusionDate || "—"}</td>
                  <td className="text-right">{p.daysUntilTransfusion ?? "—"}</td>
                  <td className="text-right text-success-dark font-semibold">{p.activeDonors}</td>
                  <td className="text-right text-warn font-semibold">{p.bufferDonors}</td>
                  <td className="text-right">
                    <button
                      onClick={() => setSelected(p)}
                      className="px-3 py-1 rounded-lg border border-brand/40 bg-brand-soft hover:bg-brand/10 text-brand text-xs font-semibold transition-colors whitespace-nowrap"
                    >
                      View calendar
                    </button>
                  </td>
                </tr>
              ))}
              {!loading && patients.length === 0 && (
                <tr>
                  <td colSpan="8" className="py-6 text-center text-muted">
                    {query ? "No bridged patients match your search." : "No bridged patients yet."}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <CalendarModal patient={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
