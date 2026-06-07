import React from "react";

export function formatApiError(err) {
  const msg = String(err?.message || err || "Unknown error");
  if (msg.includes("Failed to fetch") || msg.includes("NetworkError")) {
    return "Cannot reach the Admin API on :8000. Start it with:\n\n.venv/bin/python -m scripts.run_admin_api";
  }
  return msg;
}

export default function ErrorModal({ message, title = "Something went wrong", onDismiss }) {
  if (!message) return null;

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-ink/40 backdrop-blur-sm"
      onClick={onDismiss}
      role="presentation"
    >
      <div
        className="card max-w-md w-full p-6 shadow-2xl border border-danger/25"
        onClick={(e) => e.stopPropagation()}
        role="alertdialog"
        aria-labelledby="error-modal-title"
        aria-describedby="error-modal-body"
      >
        <div className="flex items-start gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-danger/10 text-danger text-lg font-bold">
            !
          </span>
          <div className="min-w-0 flex-1">
            <h3 id="error-modal-title" className="font-head font-semibold text-ink text-lg">
              {title}
            </h3>
            <p id="error-modal-body" className="mt-2 text-sm text-body leading-relaxed whitespace-pre-wrap break-words">
              {message}
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={onDismiss}
          className="mt-5 w-full px-4 py-2.5 rounded-xl bg-brand hover:bg-brand-dark text-white text-sm font-semibold transition-colors"
        >
          Dismiss
        </button>
      </div>
    </div>
  );
}
