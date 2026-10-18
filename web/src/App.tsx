import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/layout/AppShell";
import { Conflicts } from "./pages/Conflicts";
import { Dashboard } from "./pages/Dashboard";
import { NotFound } from "./pages/NotFound";
import { QuickTest } from "./pages/QuickTest";
import { Records } from "./pages/Records";
import { LEGACY_HASH, PATHS } from "./routes";

/** «/» and the old «/#batch»-style links land on the matching page. */
function RootRedirect() {
  const legacy = LEGACY_HASH[window.location.hash.slice(1)];
  return <Navigate to={legacy ?? PATHS.dashboard} replace />;
}

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<RootRedirect />} />
        <Route path="dashboard" element={<Dashboard />} />
        <Route path="records/:id?" element={<Records />} />
        <Route path="conflicts/:id?" element={<Conflicts />} />
        <Route path="parse" element={<QuickTest />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
