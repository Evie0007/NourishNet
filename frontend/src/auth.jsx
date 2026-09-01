import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { api, clearToken, getToken, setToken, setUnauthorizedHandler } from "./api";

const AuthContext = createContext(null);

/** Where each role lands after signing in (FR-1.4). */
export function homePathFor(user) {
  if (!user) return "/";
  return user.role === "org_coordinator" ? "/organizer" : "/staff";
}

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  // Starts true so a page refresh doesn't flash the login screen while we
  // re-verify the stored token.
  const [restoring, setRestoring] = useState(true);

  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null));

    if (!getToken()) {
      setRestoring(false);
      return;
    }
    // Ask the server who this token belongs to rather than trusting a
    // cached user object — role changes and deactivations take effect on
    // the next load this way.
    api
      .me()
      .then(setUser)
      .catch(() => clearToken())
      .finally(() => setRestoring(false));
  }, []);

  const login = useCallback(async (email, password) => {
    const res = await api.login(email, password);
    setToken(res.access_token);
    setUser(res.user);
    return res.user;
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      // A failed logout call shouldn't strand someone in a signed-in UI.
    }
    clearToken();
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, restoring, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}

/**
 * Client-side route guard. This is a usability affordance only — every
 * endpoint enforces its own role check server-side (FR-2.5). Removing this
 * component would make the UI confusing, not insecure.
 */
export function ProtectedRoute({ roles, children }) {
  const { user, restoring } = useAuth();
  const location = useLocation();

  if (restoring) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center text-sm text-gray-500">
        Loading…
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/" state={{ from: location.pathname }} replace />;
  }

  if (roles && !roles.includes(user.role)) {
    // Signed in, wrong door — send them to their own dashboard rather than
    // to a dead end.
    return <Navigate to={homePathFor(user)} replace />;
  }

  return children;
}
