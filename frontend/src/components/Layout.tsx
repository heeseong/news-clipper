import { NavLink, Outlet } from "react-router-dom";

import { useJobEvents, useLive } from "../api/events";
import { setReviewer, useReviewer } from "../reviewer";

function ReviewerName() {
  const name = useReviewer();
  return (
    <label className="reviewer" title="변경 기록과 편집 잠금에 쓰입니다 (로그인 전 임시)">
      검수자{" "}
      <input
        defaultValue={name}
        placeholder="이름"
        size={8}
        onBlur={(e) => setReviewer(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        }}
      />
    </label>
  );
}

export function Layout() {
  useJobEvents();
  const live = useLive();
  return (
    <div className="app">
      <header className="topbar">
        <NavLink to="/" className="brand">
          News Clipper
        </NavLink>
        <nav>
          <NavLink to="/jobs" end>
            작업
          </NavLink>
          <NavLink to="/upload">녹화본 올리기</NavLink>
          <NavLink to="/publish">게시 현황</NavLink>
          <NavLink to="/channels">채널</NavLink>
          <NavLink to="/admin">관리</NavLink>
        </nav>
        <ReviewerName />
        <span className={`live-dot ${live ? "on" : ""}`} title={live ? "실시간 갱신 중" : "연결 끊김: 주기적으로 갱신"} />
      </header>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
