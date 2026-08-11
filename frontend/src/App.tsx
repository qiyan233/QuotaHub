import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { QuotaProvider } from "@/contexts/QuotaContext";
import { AppLayout } from "@/layouts/AppLayout";
import AccountDetailPage from "@/pages/AccountDetailPage";
import AccountsPage from "@/pages/AccountsPage";
import AllUsagePage from "@/pages/AllUsagePage";
import DashboardPage from "@/pages/DashboardPage";
import LoginPage from "@/pages/LoginPage";
import OverviewPage from "@/pages/OverviewPage";
import SettingsPage from "@/pages/SettingsPage";
import { getToken } from "@/lib/api";

function RequireAuth({ children }: { children: React.ReactElement }) {
  if (!getToken()) {
    return <Navigate to="/login" replace />;
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
