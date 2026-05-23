import { Routes, Route } from "react-router-dom";
import { useEffect } from "react";
import { useUiStore } from "./stores/uiStore";
import { ListPage } from "./pages/ListPage";
import { DetailPage } from "./pages/DetailPage";

export default function App() {
  const { connectAgentsWs } = useUiStore();

  useEffect(() => {
    connectAgentsWs();
  }, [connectAgentsWs]);

  return (
    <main className="app-shell">
      <Routes>
        <Route path="/" element={<ListPage />} />
        <Route path="/videos/:id" element={<DetailPage />} />
      </Routes>
    </main>
  );
}
