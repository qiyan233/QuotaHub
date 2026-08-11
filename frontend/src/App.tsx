import { useEffect, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { QuotaProvider } from "@/contexts/QuotaContext";
import { AppLayout } from "@/layouts/AppLayout";
import AccountDetailPage from "@/pages/AccountDetailPage";
import AccountsPage from "@/pages/AccountsPage";
import AllUsagePage from "@/pages/AllUsagePage";
import DashboardPage from "@/pages/DashboardPage";
import LoginPage from "@/pages/LoginPage";
import OverviewPage from "@/pages/OverviewPage";
import SettingsPage from "@/pages/SettingsPage";
import { api, type SessionResponse } from "@/lib/api";

type AuthState =
  | { status: "loading" }
  | { status: "anonymous" }
  | { status: "authenticated"; session: SessionResponse };

function RequireAuth({ children }: { children: React.ReactElement }) {
  const location = useLocation();
  const [auth, setAuth] = useState<AuthState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const session = await api.session();
        if (cancelled) return;
        setAuth(session.authenticated ? { status: "authenticated", session } : { status: "anonymous" });
      } catch {
        if (!cancelled) setAuth({ status: "anonymous" });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [location.pathname]);

  if (auth.status === "loading") {
    return <div className="flex min-h-screen items-center justify-center text-sm text-muted-foreground">加载中…</div>;
  }
  if (auth.status === "anonymous") {
    return <Navigate to="/login" replace />;
  }

  // First login uses the default credential — force a password change before
  // the panel can be used (except on the change-password page itself).
  if (auth.session.must_change_password && location.pathname !== "/settings") {
    return <Navigate to="/settings?force=1" replace />;
  }
  return children;
}

export default function App() {
  return (
    <BrowserRouter>
      <QuotaProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route
            element={
              <RequireAuth>
                <AppLayout />
              </RequireAuth>
            }
          >
            <Route index element={<OverviewPage />} />
            <Route path="overview" element={<Navigate to="/" replace />} />
            <Route path="quota" element={<DashboardPage />} />
            <Route path="usage-all" element={<AllUsagePage />} />
            <Route path="accounts" element={<AccountsPage />} />
            <Route path="accounts/opencode/:id" element={<AccountDetailPage />} />
            <Route path="settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </QuotaProvider>
    </BrowserRouter>
  );
}
