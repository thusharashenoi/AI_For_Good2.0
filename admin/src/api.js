// API base: in dev, Vite proxies /api -> local FastAPI. In prod, set
// VITE_API_BASE to the AdminApi URL at build time.
const BASE = import.meta.env.VITE_API_BASE || "/api";

async function get(path) {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

async function post(path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const detail = err.detail;
    throw new Error(typeof detail === "string" ? detail : `${path} -> ${res.status}`);
  }
  return res.json();
}

export const api = {
  dashboard: (atRiskLimit = 12) => get(`/dashboard?at_risk_limit=${atRiskLimit}`),
  stats: () => get("/stats"),
  bridges: () => get("/bridges"),
  bridge: (id) => get(`/bridges/${id}`),
  unbridged: () => get("/unbridged"),
  atRisk: (limit = 15) => get(`/at-risk?limit=${limit}`),
  candidates: (pid, limit = 20) => get(`/candidates/${pid}?limit=${limit}`),
  graphOverview: (top = 8) => get(`/graph/overview?top_per_patient=${top}`),
  graphBridge: (id, top = 12) => get(`/graph/bridge/${id}?top_candidates=${top}`),
  graphPatient: (id, top = 14) => get(`/graph/patient/${id}?top_candidates=${top}`),
  emergencyCities: () => get("/emergency/cities"),
  emergencyMatch: (body) => post("/emergency/match", body),
};

export const shortId = (id) => (id ? `${id.slice(0, 8)}…` : "—");
