import { Navigate, Route, Routes } from "react-router-dom";
import { Spinner } from "@/components/ui";
import { useAuth } from "@/hooks/useAuth";
import AppLayout from "@/layouts/AppLayout";
import AccessControlPage from "@/pages/AccessControlPage";
import AgentsPage from "@/pages/AgentsPage";
import AuditLogsPage from "@/pages/AuditLogsPage";
import ConnectPage from "@/pages/ConnectPage";
import DashboardPage from "@/pages/DashboardPage";
import LoginPage from "@/pages/LoginPage";
import MemoryDetailPage from "@/pages/MemoryDetailPage";
import MemoryExplorerPage from "@/pages/MemoryExplorerPage";
import PlaygroundPage from "@/pages/PlaygroundPage";
import ReviewPage from "@/pages/ReviewPage";
import TeamsPage from "@/pages/TeamsPage";

export default function App() {
  const { me, loading } = useAuth();
  if (loading) return <Spinner label="Restoring session…" />;
  if (!me) {
    return (
      <Routes>
        <Route path="*" element={<LoginPage />} />
      </Routes>
    );
  }
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<DashboardPage />} />
        <Route path="memories" element={<MemoryExplorerPage />} />
        <Route path="memories/:id" element={<MemoryDetailPage />} />
        <Route path="playground" element={<PlaygroundPage />} />
        <Route path="agents" element={<AgentsPage />} />
        <Route path="connect" element={<ConnectPage />} />
        <Route path="teams" element={<TeamsPage />} />
        <Route path="access" element={<AccessControlPage />} />
        <Route path="review" element={<ReviewPage />} />
        <Route path="audit" element={<AuditLogsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
