import { Link, Navigate, Route, Routes } from "react-router-dom";

import { Layout } from "./components/Layout";
import { ChannelsPage } from "./pages/ChannelsPage";
import { ClipEditorPage } from "./pages/ClipEditorPage";
import { JobDetailPage } from "./pages/JobDetailPage";
import { JobListPage } from "./pages/JobListPage";
import { PublishPage } from "./pages/PublishPage";
import { UploadPage } from "./pages/UploadPage";

export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Navigate to="/jobs" replace />} />
        <Route path="jobs" element={<JobListPage />} />
        <Route path="jobs/:id" element={<JobDetailPage />} />
        <Route path="jobs/:id/clips/:clipId" element={<ClipEditorPage />} />
        <Route path="upload" element={<UploadPage />} />
        <Route path="publish" element={<PublishPage />} />
        <Route path="channels" element={<ChannelsPage />} />
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
