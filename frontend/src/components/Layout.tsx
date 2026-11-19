import { NavLink, Outlet } from "react-router-dom";

import { useJobEvents, useLive } from "../api/events";

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
          <NavLink to="/upload">업로드</NavLink>
        </nav>
        <span className={`live-dot ${live ? "on" : ""}`} title={live ? "실시간 갱신 중" : "연결 끊김: 주기적으로 갱신"} />
      </header>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
