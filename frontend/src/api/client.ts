// Thin fetch wrapper. Same origin: Vite proxies /api in development, nginx in Docker.

import { getReviewer } from "../reviewer";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public problems: string[] = [],
    public detail: unknown = null,
  ) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown, raw?: Blob): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (raw !== undefined) headers["Content-Type"] = raw.type || "application/octet-stream";
  const reviewer = getReviewer();
  if (reviewer) headers["X-Reviewer"] = encodeURIComponent(reviewer);
  const res = await fetch(path, {
    method,
    headers,
    body: raw ?? (body === undefined ? undefined : JSON.stringify(body)),
  });
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    let problems: string[] = [];
    let detail: unknown = null;
    try {
      const data = await res.json();
      detail = data?.detail;
      if (typeof data?.detail === "string") message = data.detail;
      else if (typeof data?.detail?.message === "string") {
        message = data.detail.message;
        problems = data.detail.problems ?? [];
      }
    } catch {
      // not JSON
    }
    throw new ApiError(res.status, message, problems, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", path, body),
  put: <T>(path: string, file: Blob) => request<T>("PUT", path, undefined, file),
  putJson: <T>(path: string, body: unknown) => request<T>("PUT", path, body),
  delete: <T>(path: string) => request<T>("DELETE", path),
};

/** Run a request that the server may refuse because reviewed clips would be replaced (409 with
 * `reviewed_clips`): ask the user, then repeat it with ?force=true. */
export async function withReviewGuard<T>(send: (force: boolean) => Promise<T>): Promise<T> {
  try {
    return await send(false);
  } catch (e) {
    const detail = e instanceof ApiError ? (e.detail as { reviewed_clips?: number[] } | null) : null;
    if (e instanceof ApiError && e.status === 409 && detail?.reviewed_clips) {
      if (window.confirm(`${e.message.replace(/ 그래도 진행하려면 force=true$/, "")}\n그래도 진행할까요?`)) return send(true);
      throw new ApiError(409, "취소했습니다");
    }
    throw e;
  }
}
