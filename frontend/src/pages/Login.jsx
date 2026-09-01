import { useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { api } from "../api";
import { homePathFor, useAuth } from "../auth";

export default function Login() {
  const { user, restoring, login } = useAuth();
  const navigate = useNavigate();

  const [form, setForm] = useState({ email: "", password: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [showRegister, setShowRegister] = useState(false);

  if (restoring) {
    return <div className="p-12 text-center text-sm text-gray-500">Loading…</div>;
  }

  // Already signed in — skip the front door.
  if (user) return <Navigate to={homePathFor(user)} replace />;

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      // One form for both roles. The server tells us which dashboard to
      // open; we never infer it from what the user typed (FR-1.4).
      const signedIn = await login(form.email, form.password);
      navigate(homePathFor(signedIn), { replace: true });
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen bg-gradient-to-b from-emerald-50 to-white">
      <div className="mx-auto flex min-h-screen max-w-5xl flex-col justify-center px-6 py-12">
        <div className="grid items-center gap-12 md:grid-cols-2">
          {/* ---- Welcome ---- */}
          <div className="space-y-6">
            <div className="flex items-center gap-3">
              <span
                aria-hidden="true"
                className="flex h-11 w-11 items-center justify-center rounded-xl bg-emerald-600 text-xl text-white"
              >
                🥬
              </span>
              <span className="text-2xl font-semibold tracking-tight text-gray-900">
                NourishNet
              </span>
            </div>

            <h1 className="text-3xl font-semibold leading-tight text-gray-900 sm:text-4xl">
              Good food, off the shelf and onto a table.
            </h1>

            <p className="max-w-md text-gray-600">
              NourishNet tracks grocery items as they approach their sell-by date
              and hands them to verified food pantries before they're thrown out.
            </p>

            <dl className="grid max-w-md grid-cols-3 gap-4 border-t border-emerald-100 pt-6 text-sm">
              <div>
                <dt className="text-gray-500">Store staff</dt>
                <dd className="mt-1 font-medium text-gray-900">
                  Review labels, publish donations, confirm pickups
                </dd>
              </div>
              <div>
                <dt className="text-gray-500">Organizers</dt>
                <dd className="mt-1 font-medium text-gray-900">
                  Browse, reserve, and collect with a QR code
                </dd>
              </div>
              <div>
                <dt className="text-gray-500">Hold window</dt>
                <dd className="mt-1 font-medium text-gray-900">
                  3 hours, then back to the pool
                </dd>
              </div>
            </dl>
          </div>

          {/* ---- Sign in ---- */}
          <div className="rounded-2xl border border-gray-200 bg-white p-8 shadow-sm">
            <h2 className="text-lg font-semibold text-gray-900">Sign in</h2>
            <p className="mt-1 text-sm text-gray-500">
              Staff and organizers use the same form.
            </p>

            {error && (
              <p
                role="alert"
                className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700"
              >
                {error}
              </p>
            )}

            <form onSubmit={handleSubmit} className="mt-6 space-y-4">
              <div>
                <label htmlFor="email" className="block text-sm font-medium text-gray-700">
                  Email
                </label>
                <input
                  id="email"
                  type="email"
                  autoComplete="username"
                  required
                  value={form.email}
                  onChange={(e) => setForm({ ...form, email: e.target.value })}
                  className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-100"
                />
              </div>

              <div>
                <label htmlFor="password" className="block text-sm font-medium text-gray-700">
                  Password
                </label>
                <input
                  id="password"
                  type="password"
                  autoComplete="current-password"
                  required
                  value={form.password}
                  onChange={(e) => setForm({ ...form, password: e.target.value })}
                  className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-100"
                />
              </div>

              <button
                type="submit"
                disabled={busy}
                className="w-full rounded-lg bg-emerald-600 px-4 py-2.5 font-medium text-white transition hover:bg-emerald-700 disabled:opacity-60"
              >
                {busy ? "Signing in…" : "Sign in"}
              </button>
            </form>

            <div className="mt-6 border-t border-gray-100 pt-4">
              <button
                onClick={() => setShowRegister((v) => !v)}
                className="text-sm font-medium text-emerald-700 hover:text-emerald-800"
              >
                {showRegister ? "← Back to sign in" : "New pantry? Register your organization"}
              </button>
              {showRegister && <RegisterOrg onDone={() => setShowRegister(false)} />}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * UC-03, the pre-authentication self-registration flow. Creates the
 * organization only — it starts unverified and cannot reserve until an
 * admin approves it (FR-7.3/7.4). The coordinator's login account is
 * still created by hand; wiring account creation into this form is the
 * next step on this flow.
 */
function RegisterOrg({ onDone }) {
  const [form, setForm] = useState({
    org_name: "",
    ein: "",
    contact_email: "",
    phone: "",
    address: "",
  });
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      await api.registerPantry(form);
      setDone(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <div className="mt-4 space-y-3 rounded-lg bg-emerald-50 p-4 text-sm text-emerald-900">
        <p className="font-medium">Registration received.</p>
        <p>
          A NourishNet admin will verify your EIN before your organization can
          reserve donations. We'll email you at{" "}
          <span className="font-medium">{form.contact_email}</span> when that's done.
        </p>
        <button onClick={onDone} className="font-medium underline">
          Back to sign in
        </button>
      </div>
    );
  }

  const field = (name, label, props = {}) => (
    <div>
      <label htmlFor={name} className="block text-sm font-medium text-gray-700">
        {label}
      </label>
      <input
        id={name}
        value={form[name]}
        onChange={(e) => setForm({ ...form, [name]: e.target.value })}
        className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-100"
        {...props}
      />
    </div>
  );

  return (
    <form onSubmit={handleSubmit} className="mt-4 space-y-3">
      {error && (
        <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </p>
      )}
      {field("org_name", "Organization name", { required: true })}
      {field("ein", "EIN", { required: true, placeholder: "94-2960297" })}
      {field("contact_email", "Contact email", { required: true, type: "email" })}
      {field("phone", "Phone")}
      {field("address", "Address")}
      <button
        type="submit"
        disabled={busy}
        className="w-full rounded-lg border border-emerald-600 px-4 py-2 text-sm font-medium text-emerald-700 hover:bg-emerald-50 disabled:opacity-60"
      >
        {busy ? "Submitting…" : "Submit registration"}
      </button>
    </form>
  );
}
