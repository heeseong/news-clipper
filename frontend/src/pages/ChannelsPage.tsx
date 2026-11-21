import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { usePrograms } from "../api/jobs";
import { pollConnect, useChannels, useConnectChannel, useDisconnectChannel, useSetProgramChannels } from "../api/publish";
import type { Channel, ConnectState, Program } from "../api/types";
import { CHANNEL_STATUS_LABEL, formatDateTime } from "../labels";
import { useQueryClient } from "@tanstack/react-query";

/** Channel connections (M7): connect with Google (device code or browser redirect), disconnect,
 * and the channels each program uploads to by default. */
export function ChannelsPage() {
  const channels = useChannels();
  const [params, setParams] = useSearchParams();
  const notice = params.get("connected") ? "채널을 연결했습니다." : null;
  const error = params.get("error");
  const active = (channels.data?.channels ?? []).filter((c) => c.status !== "disconnected");

  return (
    <div className="channels-page">
      <h1>채널</h1>
      {notice && <p className="ok-box">{notice}</p>}
      {error && (
        <p className="error-box">
          {error} <button className="link" onClick={() => setParams({})}>닫기</button>
        </p>
      )}
      <ConnectBox modes={channels.data?.modes ?? []} />
      <h2>연결된 채널</h2>
      {channels.isLoading ? (
        <p className="muted">불러오는 중…</p>
      ) : (channels.data?.channels.length ?? 0) === 0 ? (
        <p className="muted">아직 연결한 채널이 없습니다.</p>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>채널</th>
              <th>상태</th>
              <th>연결</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {channels.data!.channels.map((c) => (
              <ChannelRow key={c.id} channel={c} />
            ))}
          </tbody>
        </table>
      )}
      <ProgramChannels channels={active} />
    </div>
  );
}

function ChannelRow({ channel: c }: { channel: Channel }) {
  const disconnect = useDisconnectChannel();
  return (
    <tr>
      <td>
        {c.avatar_url && <img className="channel-avatar" src={c.avatar_url} alt="" />}
        {c.url ? (
          <a href={c.url} target="_blank" rel="noreferrer">
            {c.name}
          </a>
        ) : (
          c.name
        )}{" "}
        <span className="muted small">{c.platform === "fake" ? "테스트(가짜)" : c.remote_channel_id}</span>
      </td>
      <td>
        <span className={`tag channel-${c.status}`}>{CHANNEL_STATUS_LABEL[c.status] ?? c.status}</span>
        {c.last_error && c.status !== "active" && <div className="small error-text">{c.last_error}</div>}
      </td>
      <td className="small">{formatDateTime(c.connected_at)}</td>
      <td>
        {c.status !== "disconnected" && (
          <button
            className="btn btn-small"
            disabled={disconnect.isPending}
            onClick={() => {
              if (window.confirm(`${c.name} 연결을 해제할까요? 저장한 토큰을 지웁니다(올린 영상은 그대로).`))
                disconnect.mutate(c.id, { onError: (e) => window.alert((e as Error).message) });
            }}
          >
            연결 해제
          </button>
        )}
      </td>
    </tr>
  );
}

function ConnectBox({ modes }: { modes: string[] }) {
  const connect = useConnectChannel();
  const qc = useQueryClient();
  const [state, setState] = useState<ConnectState | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Device flow: ask the server every `interval` seconds until Google says yes or no.
  useEffect(() => {
    if (!state || state.kind !== "device" || state.status !== "pending" || !state.session_id) return;
    const timer = window.setTimeout(() => {
      pollConnect(state.session_id!)
        .then((s) => {
          setState(s);
          if (s.status === "connected") void qc.invalidateQueries({ queryKey: ["channels"] });
        })
        .catch((e: Error) => setError(e.message));
    }, Math.max(2, state.interval) * 1000);
    return () => window.clearTimeout(timer);
  }, [state, qc]);

  const start = (mode: "fake" | "web" | "device") => {
    setError(null);
    connect.mutate(mode, {
      onSuccess: (s) => {
        if (s.kind === "web" && s.auth_url) window.location.assign(s.auth_url);
        else setState(s);
      },
      onError: (e) => setError((e as Error).message),
    });
  };

  return (
    <div className="connect-box">
      <h2>채널 연결</h2>
      {modes.length === 0 && (
        <p className="small warn-text">
          Google OAuth 클라이언트가 설정되지 않았습니다 (서버 설정 GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, TOKEN_ENCRYPTION_KEY).
        </p>
      )}
      <div className="row">
        {modes.includes("device") && (
          <button className="btn btn-primary" disabled={connect.isPending} onClick={() => start("device")}>
            Google 계정으로 연결 (코드 입력)
          </button>
        )}
        {modes.includes("web") && (
          <button className="btn" disabled={connect.isPending} onClick={() => start("web")}>
            Google 로그인 화면으로 연결
          </button>
        )}
        {modes.includes("fake") && (
          <button className="btn" disabled={connect.isPending} onClick={() => start("fake")}>
            테스트 채널 추가 (가짜, 개발용)
          </button>
        )}
      </div>
      {error && <p className="error-box">{error}</p>}
      {state?.kind === "device" && state.status === "pending" && (
        <div className="device-code">
          <p>
            <a href={state.verification_url ?? "https://www.google.com/device"} target="_blank" rel="noreferrer">
              {state.verification_url}
            </a>{" "}
            에서 아래 코드를 입력하고, 업로드할 <strong>채널(브랜드 계정)</strong>을 고른 뒤 YouTube 권한을 허용하세요.
          </p>
          <div className="user-code">{state.user_code}</div>
          <p className="small muted">기다리는 중… (만료 {formatDateTime(state.expires_at)})</p>
        </div>
      )}
      {state?.status === "failed" && <p className="error-box">{state.error}</p>}
      {state?.status === "connected" && state.channel && <p className="ok-box">{state.channel.name} 채널을 연결했습니다.</p>}
    </div>
  );
}

function ProgramChannels({ channels }: { channels: Channel[] }) {
  const programs = usePrograms();
  const save = useSetProgramChannels();
  if (!programs.data?.length || !channels.length) return null;
  const toggle = (p: Program, id: number, on: boolean) => {
    const next = on ? [...(p.default_channels ?? []), id] : (p.default_channels ?? []).filter((x) => x !== id);
    save.mutate({ program: p, channels: next }, { onError: (e) => window.alert((e as Error).message) });
  };
  return (
    <>
      <h2>프로그램 기본 채널</h2>
      <p className="small muted">새로 만드는 클립의 업로드 채널입니다. 클립마다 편집기에서 바꿀 수 있습니다. 채널이 없으면 렌더까지만 하고 올리지 않습니다.</p>
      <table className="table">
        <thead>
          <tr>
            <th>프로그램</th>
            {channels.map((c) => (
              <th key={c.id}>{c.name}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {programs.data.map((p) => (
            <tr key={p.id}>
              <td>{p.name}</td>
              {channels.map((c) => (
                <td key={c.id}>
                  <input
                    type="checkbox"
                    aria-label={`${p.name} → ${c.name}`}
                    checked={(p.default_channels ?? []).includes(c.id)}
                    disabled={save.isPending || c.status !== "active"}
                    onChange={(e) => toggle(p, c.id, e.target.checked)}
                  />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
