import { Link, Navigate, Route, Routes } from "react-router-dom";

import { Layout } from "./components/Layout";
import { JobDetailPage } from "./pages/JobDetailPage";
import { JobListPage } from "./pages/JobListPage";

export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Navigate to="/jobs" replace />} />
        <Route path="jobs" element={<JobListPage />} />
        <Route path="jobs/:id" element={<JobDetailPage />} />
        <Route
          path="*"
          element={
            <p>
              페이지를 찾을 수 없습니다. <Link to="/jobs">작업 목록으로</Link>
            </p>
          }
        />
      </Route>
    </Routes>
  );
}
