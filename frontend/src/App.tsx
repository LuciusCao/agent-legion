import { Routes, Route, useLocation } from "react-router-dom";
import { useEffect } from "react";
import { useUiStore } from "./stores/uiStore";
import { ListPage } from "./pages/ListPage";
import { DetailPage } from "./pages/DetailPage";

export default function App() {
  const { connectAgentsWs, closeAddDialog } = useUiStore();
  const location = useLocation();

  useEffect(() => {
    connectAgentsWs();
  }, [connectAgentsWs]);

  // Close any open dialogs on route change so that navigating away
  // from the list page (e.g. into a video detail) always resets UI
  // state and prevents dialogs from re-appearing on return.
  useEffect(() => {
    closeAddDialog();
  }, [location.pathname, closeAddDialog]);

  return (
    <main className="app-shell">
      <Routes>
        <Route path="/" element={<ListPage />} />
        <Route path="/videos/:id" element={<DetailPage />} />
      </Routes>
    </main>
  );
}
