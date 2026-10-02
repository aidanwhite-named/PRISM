import React from "react";
import ReactDOM from "react-dom/client";
import {
  HashRouter,
  Route,
  Routes,
} from "react-router-dom";

import App from "./App";
import { RunSessionProvider } from "./lib/runSession";
import WorkspaceEntry from "./components/WorkspaceEntry";
import HistoryPage from "./pages/HistoryPage";
import RunPage from "./pages/RunPage";
import SettingsPage from "./pages/SettingsPage";
import AnswersPage from "./pages/AnswersPage";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {/* 실행 상태는 라우터 바깥에 둔다. 메뉴를 옮겨도 결과가 남아야 한다. */}
    <RunSessionProvider>
      <HashRouter
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <Routes>
          <Route path="/" element={<App />}>
            <Route index element={<WorkspaceEntry />} />
            {/* 두 작업은 각자의 주소를 갖는다. 즐겨찾기도 뒤로 가기도 어느
                작업이었는지 기억한다. */}
            <Route
              path="analysis"
              element={<RunPage kind="patent_analysis" />}
            />
            <Route
              path="search"
              element={<RunPage kind="similarity_search" />}
            />
            <Route path="answers" element={<AnswersPage />} />
            <Route path="history" element={<HistoryPage />} />
            <Route path="settings" element={<SettingsPage />} />
            {/* 주소를 나누기 전의 즐겨찾기와 세션 캐시. ?job= 이 붙어 있으면
                작업 화면이 그 실행의 종류를 읽고 제 주소로 다시 보낸다. */}
            <Route path="run" element={<WorkspaceEntry resume />} />
            <Route path="*" element={<WorkspaceEntry />} />
          </Route>
        </Routes>
      </HashRouter>
    </RunSessionProvider>
  </React.StrictMode>,
);
