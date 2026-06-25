import axios from "axios";

// 会话凭据走 httponly+secure cookie（ch_token），JS 读不到也不再存 localStorage。
// 这里只存一个非敏感的“已登录”标记，用于驱动前端的登录态 UI；真正的鉴权是 cookie。
const AUTH_FLAG_KEY = "ch_authed";
const KNOCK_KEY = "ch_knock";

export function getToken(): string {
  return localStorage.getItem(AUTH_FLAG_KEY) || "";
}
export function setToken(t: string): void {
  // 不持久化真实 token；仅记录登录态标记（登录响应里 token 非空 = 已登录）。
  if (t) localStorage.setItem(AUTH_FLAG_KEY, "1");
  else localStorage.removeItem(AUTH_FLAG_KEY);
}
export function getKnock(): string {
  return sessionStorage.getItem(KNOCK_KEY) || localStorage.getItem(KNOCK_KEY) || "";
}
export function setKnock(s: string): void {
  // knock 改存 sessionStorage（随标签页关闭失效），并清掉历史遗留的 localStorage 值。
  localStorage.removeItem(KNOCK_KEY);
  if (s) sessionStorage.setItem(KNOCK_KEY, s);
  else sessionStorage.removeItem(KNOCK_KEY);
}

// 启动时从 URL ?key= 抓 secret 并存入 sessionStorage（一次性：抓到即从 URL 抹掉）
export function captureKnockFromUrl(): boolean {
  const params = new URLSearchParams(window.location.search);
  const k = params.get("key") || params.get("knock");
  if (k) {
    setKnock(k);
    // 清掉 URL 里的 key，避免泄露
    params.delete("key");
    params.delete("knock");
    const qs = params.toString();
    const newUrl = window.location.pathname + (qs ? "?" + qs : "") + window.location.hash;
    window.history.replaceState({}, "", newUrl);
    return true;
  }
  return false;
}

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || "/api",
  timeout: 120_000,
  // 让浏览器自动带上 httponly cookie（同源），鉴权不再依赖 JS 注入的 token 头。
  withCredentials: true,
});

api.interceptors.request.use((cfg) => {
  // 不再注入 X-Auth-Token：浏览器会自动带 ch_token cookie。
  // knock 仍以请求头发送（不进 URL / 日志）。
  const k = getKnock();
  if (k) cfg.headers["X-Knock-Secret"] = k;
  return cfg;
});

api.interceptors.response.use(
  (r) => r,
  (err) => {
    const status = err?.response?.status;
    const msg = err?.response?.data?.detail || err?.message || "请求失败";
    if (status === 401) {
      setToken("");
      window.dispatchEvent(new Event("ch-auth-changed"));
    }
    if (status === 404 && err?.config?.url) {
      // knock 失败也会返回 404，区分一下：如果 url 是 /health 等公开路径不算
      const url = String(err.config.url);
      const isApi = url.startsWith("/") && !url.includes("/health");
      if (isApi && !getKnock()) {
        // 引导用户到 knock 缺失页面
        window.dispatchEvent(new Event("ch-knock-missing"));
      }
    }
    return Promise.reject(new Error(msg));
  },
);

export type Provider = "aws" | "gcp" | "oracle" | "azure";

export interface Account {
  id: number;
  name: string;
  provider: Provider;
  default_region: string;
  enabled: boolean;
  group_tag: string;
  note: string;
  monthly_traffic_gb: number;
  credit_total_usd: number;
  credit_used_usd: number;
  credit_expires_at: string | null;
  created_at: string;
}

export interface Instance {
  id: string;
  name: string;
  state: string;
  region: string;
  zone: string;
  instance_type: string;
  public_ip: string;
  private_ip: string;
  tags: Record<string, string>;
  image: string;
  arch: string;
  vcpus: number;
  memory_mb: number;
  disk_gb: number;
  launched_at: string | null;
  security_groups: string[];
  traffic_limit_gb: number;
  monthly_traffic_gb: number;
  monthly_traffic_out_gb: number;
  monthly_traffic_pct: number;
  auto_stopped_by_traffic: boolean;
  last_alive_at: string | null;
  last_collect_error: string;
  ssh_user: string;
  ssh_port: number;
  iface: string;
  has_ssh_password: boolean;
  cpu_pct: number;
  mem_pct: number;
  mem_total_mb: number;
  mem_used_mb: number;
  load1: number;
  load5: number;
  uptime_sec: number;
  hourly_usd: number;
  daily_usd: number;
  account_id: number;
  account_name: string;
  account_provider: string;
}

export interface BudgetSummary {
  account_id: number;
  account_name: string;
  provider: string;
  credit_total_usd: number;
  credit_used_usd: number;
  credit_remaining_usd: number;
  credit_expires_at: string | null;
  days_to_expiry: number | null;
  daily_burn_usd: number;
  monthly_burn_usd: number;
  days_until_credit_runs_out: number | null;
  will_outlast_expiry: boolean | null;
  instances: {
    id: string; name: string; instance_type: string; region: string; state: string;
    hourly_usd: number; daily_usd: number;
  }[];
}

export interface FirewallRule {
  id: string;
  direction: "ingress" | "egress";
  protocol: "tcp" | "udp" | "icmp" | "all";
  port_range: string;
  cidrs: string[];
  description: string;
  target: string;
}

export interface Schedule {
  id: number;
  account_id: number;
  instance_id: string;
  action: "start" | "stop" | "restart" | "destroy";
  trigger_type: "cron" | "date";
  cron: string;
  run_at: string | null;
  enabled: boolean;
  note: string;
  created_at: string;
}

export interface SSHKey {
  id: number;
  name: string;
  public_key: string;
  is_default: boolean;
  created_at: string;
}

export interface DashboardSummary {
  accounts_total: number;
  accounts_by_provider: Record<string, number>;
  instances_total: number;
  instances_running: number;
  instances_stopped: number;
  monthly_traffic_gb_total: number;
  over_limit_count: number;
  last_collected_at: string | null;
}

export interface AuditEntry {
  id: number;
  at: string;
  actor: string;
  action: string;
  target: string;
  detail: Record<string, unknown>;
  ok: boolean;
  error: string;
}

export interface BulkResult {
  total: number;
  ok: number;
  failed: number;
  results: { target: Record<string, string>; ok: boolean; error?: string }[];
}
