import { Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, ProtectedRoute } from "./auth";
import Login from "./pages/Login";
import StaffDashboard from "./pages/StaffDashboard";
import OrganizerDashboard from "./pages/OrganizerDashboard";

const STORE_ROLES = ["staff", "manager", "admin"];

function App() {
  return (
    <AuthProvider>
      <Routes>
        {/* Welcome + sign in. Redirects to the right dashboard if already
            signed in, so this doubles as the post-logout landing page. */}
        <Route path="/" element={<Login />} />

        <Route
          path="/staff"
          element={
            <ProtectedRoute roles={STORE_ROLES}>
              <StaffDashboard />
            </ProtectedRoute>
          }
        />

        <Route
          path="/organizer"
          element={
            <ProtectedRoute roles={["org_coordinator"]}>
              <OrganizerDashboard />
            </ProtectedRoute>
          }
        />

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthProvider>
  );
}

export default App;
