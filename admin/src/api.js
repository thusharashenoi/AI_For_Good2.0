// API base: in dev, Vite proxies /api -> local FastAPI. In prod, set
// VITE_API_BASE to the AdminApi URL at build time.
const BASE = import.meta.env.VITE_API_BASE || "/api";

async function parseErrorResponse(path, res) {
  const text = await res.text().catch(() => "");
  let detail;
  try {
    detail = JSON.parse(text).detail;
  } catch {
    detail = undefined;
  }
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail) && detail.length) {
    return detail.map((d) => d.msg || JSON.stringify(d)).join("; ");
  }
  if (res.status === 500) {
    if (text.includes("ECONNREFUSED") || text.includes("proxy") || !text.trim()) {
      return "Admin API is not running on :8000. Start it:\n\n.venv/bin/python -m scripts.run_admin_api";
    }
    return text.slice(0, 300) || `Admin API error on ${path}. Check the terminal running uvicorn on :8000.`;
  }
  return `${path} -> ${res.status}`;
}

async function get(path) {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(await parseErrorResponse(path, res));
  return res.json();
}

async function post(path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new Error(await parseErrorResponse(path, res));
  }
  return res.json();
}

async function del(path) {
  const res = await fetch(`${BASE}${path}`, { method: "DELETE" });
  if (!res.ok) {
    throw new Error(await parseErrorResponse(path, res));
  }
  return res.json();
}

export const api = {
  dashboard: (atRiskLimit = 12) => get(`/dashboard?at_risk_limit=${atRiskLimit}`),
  stats: () => get("/stats"),
  requests: (limit = 20) => get(`/requests?limit=${limit}`),
  deleteRequest: (requestId) => del(`/requests/${requestId}`),
  deleteAppointment: (appointmentId) => del(`/appointments/${appointmentId}`),
  formBridge: (patientId) => post(`/bridges/form/${patientId}`, {}),
  broadcastBridge: (bridgeId) => post(`/bridges/${bridgeId}/broadcast`, {}),
  broadcastOutreachStatus: (bridgeId) => get(`/bridges/${bridgeId}/outreach-status`),
  bridges: () => get("/bridges"),
  bridge: (id) => get(`/bridges/${id}`),
  unbridged: () => get("/unbridged"),
  atRisk: (limit = 15) => get(`/at-risk?limit=${limit}`),
  candidates: (pid, limit = 20) => get(`/candidates/${pid}?limit=${limit}`),
  graphOverview: (top = 8) => get(`/graph/overview?top_per_patient=${top}`),
  graphBridge: (id, top = 12) => get(`/graph/bridge/${id}?top_candidates=${top}`),
  graphPatient: (id, top = 14) => get(`/graph/patient/${id}?top_candidates=${top}`),
  calendarPatients: (q = "") => get(`/calendar/patients${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  calendarPatient: (patientId) => get(`/calendar/patient/${patientId}`),
  emergencyCities: () => get("/emergency/cities"),
  emergencyMatch: (body) => post("/emergency/match", body),
};

export const shortId = (id) => (id ? `${id.slice(0, 8)}…` : "—");
