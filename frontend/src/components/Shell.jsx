import { useAuth } from "../auth";

const ROLE_LABELS = {
  staff: "Store staff",
  manager: "Store manager",
  admin: "Platform admin",
  org_coordinator: "Organizer",
};

/** Header + page frame shared by both dashboards. */
export default function Shell({ title, subtitle, children }) {
  const { user, logout } = useAuth();

  return (
    <div className="min-h-screen bg-gray-50 text-gray-900">
      <header className="border-b border-gray-200 bg-white">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-6 py-3">
          <div className="flex items-center gap-2.5">
            <span
              aria-hidden="true"
              className="flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-600 text-white"
            >
              🥬
            </span>
            <span className="font-semibold tracking-tight">NourishNet</span>
            <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-600">
              {ROLE_LABELS[user?.role] || user?.role}
            </span>
          </div>

          <div className="flex items-center gap-3 text-sm">
            <span className="text-gray-500">{user?.full_name || user?.email}</span>
            <button
              onClick={logout}
              className="rounded-lg border border-gray-300 px-3 py-1.5 font-medium hover:bg-gray-100"
            >
              Sign out
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-6 py-8">
        <div className="mb-6">
          <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
          {subtitle && <p className="mt-1 text-sm text-gray-500">{subtitle}</p>}
        </div>
        {children}
      </main>
    </div>
  );
}

export function Card({ title, action, children, className = "" }) {
  return (
    <section className={`rounded-xl border border-gray-200 bg-white ${className}`}>
      {(title || action) && (
        <div className="flex items-center justify-between gap-3 border-b border-gray-100 px-4 py-3">
          <h2 className="font-medium">{title}</h2>
          {action}
        </div>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function ErrorBanner({ message, onDismiss }) {
  if (!message) return null;
  return (
    <div
      role="alert"
      className="mb-6 flex items-start justify-between gap-3 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-800"
    >
      <span>{message}</span>
      {onDismiss && (
        <button onClick={onDismiss} className="font-medium underline" aria-label="Dismiss error">
          Dismiss
        </button>
      )}
    </div>
  );
}

/** NFR-4.5.2: status is conveyed by its text label, never by colour alone. */
const STATUS_STYLES = {
  in_stock: "bg-gray-100 text-gray-700",
  near_expiry: "bg-amber-100 text-amber-800",
  needs_review: "bg-amber-100 text-amber-800",
  available: "bg-emerald-100 text-emerald-800",
  reserved: "bg-blue-100 text-blue-800",
  picked_up: "bg-gray-100 text-gray-600",
  expired_hold: "bg-gray-100 text-gray-600",
  discarded: "bg-gray-100 text-gray-600",
  pending: "bg-blue-100 text-blue-800",
  expired: "bg-gray-100 text-gray-600",
  cancelled: "bg-gray-100 text-gray-600",
};

export function StatusBadge({ status }) {
  const label = String(status).replace(/_/g, " ");
  return (
    <span
      className={`inline-block whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ${
        STATUS_STYLES[status] || "bg-gray-100 text-gray-700"
      }`}
    >
      {label}
    </span>
  );
}

export function Empty({ children }) {
  return <p className="py-6 text-center text-sm text-gray-500">{children}</p>;
}
