import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import "@xterm/xterm/css/xterm.css";
import { motion, AnimatePresence } from "framer-motion";
import { api, Instance } from "../lib/api";
import { PageHeader, ProviderTag, StateBadge } from "../lib/components";

interface ShellTab {
  id: string;            // accountId:instanceId
  account_id: number;
  instance_id: string;
  name: string;
  provider: string;
  ip: string;
  ssh_user: string;
  ssh_port: number;
  has_password: boolean;
}

export default function ShellPage() {
  const [params, setParams] = useSearchParams();
  const initial = params.get("open"); // "accountId:instanceId"
  const [tabs, setTabs] = useState<ShellTab[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [search, setSearch] = useState("");

  const list = useQuery({
    queryKey: ["fleet-instances"],
    queryFn: async () => (await api.get<Instance[]>("/fleet/instances")).data,
    refetchInterval: 60_000,
  });

  const filtered = useMemo(() => {
    const items = list.data || [];
    const s = search.toLowerCase();
    return items
      .filter((i) => i.public_ip)
      .filter((i) => !s || i.id.includes(s) || i.name.toLowerCase().includes(s) || i.public_ip.includes(s));
  }, [list.data, search]);

  // 处理 ?open=xxx
  useEffect(() => {
    if (!initial || !list.data) return;
    const [accId, instId] = initial.split(":");
    const inst = list.data.find((i) => String(i.account_id) === accId && i.id === instId);
    if (inst) {
      openTab(inst);
      setParams({}, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initial, list.data]);

  function openTab(i: Instance) {
    const id = `${i.account_id}:${i.id}`;
    if (!tabs.find((t) => t.id === id)) {
      setTabs((prev) => [...prev, {
        id, account_id: i.account_id, instance_id: i.id,
        name: i.name || i.id, provider: i.account_provider,
        ip: i.public_ip, ssh_user: i.ssh_user || "root",
        ssh_port: i.ssh_port || 22, has_password: i.has_ssh_password,
      }]);
    }
    setActive(id);
  }

  function closeTab(id: string) {
    setTabs((prev) => prev.filter((t) => t.id !== id));
    setActive((cur) => (cur === id ? (tabs.find((t) => t.id !== id)?.id ?? null) : cur));
  }

  return (
    <div>
      <PageHeader
        title="终端"
        description={`${tabs.length} 个会话 · ${filtered.length} 台可连实例`}
      />
      <div className="grid grid-cols-[280px_1fr] gap-4 h-[calc(100vh-180px)]">
        {/* 左侧实例列表 */}
        <div className="glass !p-0 overflow-hidden flex flex-col">
          <div className="p-3 border-b border-white/40">
            <input className="input" placeholder="搜索实例" value={search} onChange={(e) => setSearch(e.target.value)} />
          </div>
          <div className="overflow-y-auto flex-1">
            {filtered.length === 0 && <div className="text-center text-slate-400 py-10 text-sm">无可连接实例</div>}
            {filtered.map((i) => {
              const id = `${i.account_id}:${i.id}`;
              const isOpen = !!tabs.find((t) => t.id === id);
              return (
                <button
                  key={id}
                  onClick={() => openTab(i)}
                  className={`w-full text-left px-3 py-2.5 border-b border-white/30 hover:bg-white/40 transition-colors ${isOpen ? "bg-indigo-50/40" : ""}`}
                >
                  <div className="flex items-center gap-2">
                    <ProviderTag provider={i.account_provider} />
                    <StateBadge state={i.state} />
                    {isOpen && <span className="ml-auto text-[10px] text-indigo-600 font-semibold">已打开</span>}
                  </div>
                  <div className="font-medium text-slate-900 mt-1.5 truncate">{i.name || i.id}</div>
                  <div className="text-[11px] text-slate-500 font-mono truncate">{i.public_ip}</div>
                </button>
              );
            })}
          </div>
        </div>

        {/* 右侧终端 */}
        <div className="glass !p-0 flex flex-col overflow-hidden">
          {/* tab bar */}
          <div className="border-b border-white/40 px-3 py-2 flex items-center gap-1 overflow-x-auto bg-gradient-to-r from-slate-50/30 to-indigo-50/20">
            {tabs.length === 0 ? (
              <div className="text-sm text-slate-400 py-1">从左侧选择实例打开终端</div>
            ) : (
              tabs.map((t) => (
                <div
                  key={t.id}
                  onClick={() => setActive(t.id)}
                  className={`flex items-center gap-2 px-3 py-1.5 rounded-lg cursor-pointer border transition-all ${
                    active === t.id
                      ? "bg-gradient-to-r from-indigo-600 to-violet-600 text-white border-transparent shadow-sm"
                      : "bg-white/60 border-white/50 text-slate-700 hover:bg-white"
                  }`}
                >
                  <ProviderTag provider={t.provider} />
                  <span className="text-sm font-medium truncate max-w-[140px]">{t.name}</span>
                  <button
                    onClick={(e) => { e.stopPropagation(); closeTab(t.id); }}
                    className={`ml-1 opacity-60 hover:opacity-100 ${active === t.id ? "text-white" : "text-slate-500"}`}
                  >×</button>
                </div>
              ))
            )}
          </div>

          {/* 终端区 */}
          <div className="flex-1 relative bg-[#0d0d12]">
            <AnimatePresence>
              {tabs.map((t) => (
                <motion.div
                  key={t.id}
                  initial={{ opacity: 0 }}
                  animate={{ opacity: active === t.id ? 1 : 0 }}
                  exit={{ opacity: 0 }}
                  className="absolute inset-0"
                  style={{ pointerEvents: active === t.id ? "auto" : "none", zIndex: active === t.id ? 1 : 0 }}
                >
                  <ShellTerm tab={t} active={active === t.id} />
                </motion.div>
              ))}
            </AnimatePresence>
            {tabs.length === 0 && (
              <div className="absolute inset-0 flex items-center justify-center text-slate-500 text-sm">
                💻 选一个实例开始
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function ShellTerm({ tab, active }: { tab: ShellTab; active: boolean }) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const dataDisposerRef = useRef<{ dispose: () => void } | null>(null);
  const resizeDisposerRef = useRef<{ dispose: () => void } | null>(null);
  const connectingRef = useRef(false);
  const [needPwd, setNeedPwd] = useState(true);     // 每次开终端都先弹密码框，不静默连
  const [pwdInput, setPwdInput] = useState("");
  const [savePwd, setSavePwd] = useState(true);     // 默认保存：输了密码就记住，下次免输
  const [status, setStatus] = useState<"idle" | "connecting" | "connected" | "error" | "closed">("idle");
  const [errMsg, setErrMsg] = useState("");

  // 初始化 xterm
  useEffect(() => {
    if (!wrapRef.current) return;
    const term = new Terminal({
      cursorBlink: true,
      fontFamily: "'JetBrains Mono', 'Menlo', 'Consolas', monospace",
      fontSize: 13,
      theme: {
        background: "#0d0d12",
        foreground: "#e2e8f0",
        cursor: "#a78bfa",
        selectionBackground: "rgba(167, 139, 250, 0.3)",
      },
      scrollback: 5000,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.loadAddon(new WebLinksAddon());
    term.open(wrapRef.current);
    // 推迟一帧 fit，给 DOM 时间布局，避免 dimensions undefined
    requestAnimationFrame(() => { try { fit.fit(); } catch (_) {} });
    termRef.current = term;
    fitRef.current = fit;

    const onResize = () => { try { fit.fit(); } catch (_) {} };
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      cleanupConnection();
      term.dispose();
      termRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 切换 active 时 fit 一下
  useEffect(() => {
    if (active) {
      setTimeout(() => { try { fitRef.current?.fit(); sendResize(); } catch (_) {} }, 50);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  function sendResize() {
    const term = termRef.current;
    const ws = wsRef.current;
    if (term && ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
    }
  }

  function cleanupConnection() {
    try { dataDisposerRef.current?.dispose(); } catch (_) {}
    try { resizeDisposerRef.current?.dispose(); } catch (_) {}
    dataDisposerRef.current = null;
    resizeDisposerRef.current = null;
    const ws = wsRef.current;
    wsRef.current = null;
    if (ws) {
      ws.onopen = null;
      ws.onmessage = null;
      ws.onerror = null;
      ws.onclose = null;
      try {
        if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
          ws.close();
        }
      } catch (_) {}
    }
  }

  async function connect(passwordOverride?: string) {
    if (connectingRef.current) {
      console.log("[shell] connect skipped: already connecting");
      return;
    }
    if (wsRef.current && wsRef.current.readyState <= WebSocket.OPEN) {
      console.log("[shell] cleanup old ws before reconnect");
      cleanupConnection();
    }
    const term = termRef.current;
    if (!term) {
      console.log("[shell] connect skipped: term not ready");
      return;
    }
    // #27 不在明文 HTTP 下打开带凭据的终端：SSH 密码会经 WS 明文传输，
    // 且鉴权 cookie 为 secure（只走 HTTPS）。localhost 开发或显式 dev override 放行。
    const insecure = location.protocol !== "https:";
    const devOverride =
      import.meta.env.VITE_ALLOW_INSECURE_SHELL === "1" ||
      location.hostname === "localhost" ||
      location.hostname === "127.0.0.1";
    if (insecure && !devOverride) {
      setStatus("error");
      setErrMsg("拒绝在非 HTTPS 下打开终端（密码会明文传输）。请用 HTTPS 访问。");
      setNeedPwd(false);
      term.clear();
      term.writeln(`\x1b[31m✗ 当前非 HTTPS 访问，已拒绝打开终端以保护 SSH 密码。\x1b[0m`);
      term.writeln(`\x1b[33m  请通过 https:// 访问；本地开发可设 VITE_ALLOW_INSECURE_SHELL=1。\x1b[0m`);
      return;
    }
    connectingRef.current = true;
    setStatus("connecting");
    setNeedPwd(false);
    setErrMsg("");
    term.clear();
    term.writeln(`\x1b[36m→ 连接 ${tab.ssh_user}@${tab.ip}:${tab.ssh_port}\x1b[0m`);
    if (passwordOverride) {
      term.writeln(`\x1b[36m→ 使用本次输入的密码\x1b[0m`);
    } else if (tab.has_password) {
      term.writeln(`\x1b[36m→ 使用已存密码\x1b[0m`);
    } else {
      term.writeln(`\x1b[36m→ 无密码，将尝试 SSH 密钥 / EIC\x1b[0m`);
    }

    // 如果勾了保存密码，先 PUT 等成功再建 WebSocket（保证密码先存上）
    if (passwordOverride && savePwd) {
      try {
        await api.put(`/accounts/${tab.account_id}/instances/${tab.instance_id}/ssh-password`,
          { password: passwordOverride });
        term.writeln(`\x1b[32m→ 密码已保存到 CloudHelper\x1b[0m`);
      } catch (e: any) {
        term.writeln(`\x1b[31m⚠ 保存密码失败：${e?.message || e}\x1b[0m`);
      }
    }

    // 鉴权依赖 httponly cookie：浏览器同源 WS 握手会自动带上 ch_token，
    // 不再把 token 放进 URL（避免泄露到 nginx 日志 / 浏览器历史）。
    // knock 是路径混淆口令（非凭据），仍走 query 以通过后端路径保护。
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const knock = sessionStorage.getItem("ch_knock") || localStorage.getItem("ch_knock") || "";
    const q = new URLSearchParams();
    if (knock) q.set("knock", knock);
    const qs = q.toString() ? `?${q.toString()}` : "";
    const url = `${proto}://${location.host}/api/ws/instances/${tab.account_id}/${tab.instance_id}/shell${qs}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      if (passwordOverride) {
        ws.send(JSON.stringify({ type: "password", data: passwordOverride }));
      }
      ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
    };
    ws.onmessage = (e) => {
      let msg: any;
      try { msg = JSON.parse(String(e.data)); } catch { return; }
      if (msg.type === "data") term.write(msg.data || "");
      else if (msg.type === "status") {
        setStatus("connected");
        setNeedPwd(false);
        term.writeln(`\x1b[32m✓ ${msg.data}\x1b[0m`);
        sendResize();
      }
      else if (msg.type === "error") {
        setStatus("error");
        setErrMsg(msg.data || "未知错误");
        term.writeln(`\x1b[31m✗ ${msg.data}\x1b[0m`);
      }
    };
    ws.onerror = () => {
      setStatus("error"); setErrMsg("WebSocket 错误");
      connectingRef.current = false;
    };
    ws.onclose = () => {
      setStatus((s) => s === "error" ? s : "closed");
      connectingRef.current = false;
      // 如果是用户主动 cleanup，dataDisposer 已经清；这里再保险一下
      try { dataDisposerRef.current?.dispose(); } catch (_) {}
      try { resizeDisposerRef.current?.dispose(); } catch (_) {}
      dataDisposerRef.current = null;
      resizeDisposerRef.current = null;
    };

    // 先解绑旧的（保险）
    try { dataDisposerRef.current?.dispose(); } catch (_) {}
    try { resizeDisposerRef.current?.dispose(); } catch (_) {}
    dataDisposerRef.current = term.onData((d) => {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "input", data: d }));
    });
    resizeDisposerRef.current = term.onResize(() => sendResize());

    // 连接结束（成功/失败）后允许下一次手动重连
    setTimeout(() => { connectingRef.current = false; }, 0);
  }

  // 自动连接（仅当无连接、不需要密码时）
  useEffect(() => {
    if (status !== "idle") return;
    if (needPwd) return;
    if (wsRef.current) return;
    connect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, needPwd]);

  return (
    <div className="absolute inset-0 flex flex-col">
      {/* 状态条 */}
      <div className="px-3 py-1.5 text-xs flex items-center gap-2 border-b border-white/10 bg-black/30 text-slate-400">
        <span className={`status-dot ${status === "connected" ? "status-dot-running" : status === "error" ? "status-dot-error" : status === "connecting" ? "status-dot-pending" : "status-dot-stopped"}`}></span>
        <span>{status === "connected" ? "已连接" : status === "connecting" ? "连接中…" : status === "error" ? `错误：${errMsg}` : status === "closed" ? "已断开" : "待连接"}</span>
        <span className="ml-auto font-mono">{tab.ssh_user}@{tab.ip}:{tab.ssh_port}</span>
        {(status === "closed" || status === "error") && (
          <button className="btn btn-sm" onClick={() => { setStatus("idle"); }}>重连</button>
        )}
      </div>

      {/* 密码输入层 */}
      {needPwd && status !== "connected" && (
        <div className="absolute inset-0 z-10 flex items-center justify-center bg-black/70 backdrop-blur-sm">
          <div className="glass w-[420px] !p-0">
            <div className="px-5 py-3 border-b border-white/40">
              <div className="text-base font-bold gradient-text">SSH 密码登录</div>
              <div className="text-xs text-slate-500 mt-0.5 font-mono">{tab.ssh_user}@{tab.ip}:{tab.ssh_port}</div>
              {tab.has_password && (
                <div className="text-[11px] text-emerald-600 mt-1">✓ 该实例已存储密码，留空直接回车即可用已存密码连接</div>
              )}
            </div>
            <div className="p-5 space-y-3">
              <div>
                <label className="label">密码 {tab.has_password && <span className="text-slate-400 font-normal">(留空用已存)</span>}</label>
                <input type="password" className="input" autoFocus value={pwdInput}
                  onKeyDown={(e) => { if (e.key === "Enter") connect(pwdInput || undefined); }}
                  onChange={(e) => setPwdInput(e.target.value)} />
              </div>
              <label className="text-xs flex items-center gap-2 text-slate-600">
                <input type="checkbox" checked={savePwd} onChange={(e) => setSavePwd(e.target.checked)} />
                覆盖保存密码（下次连接也用此密码）
              </label>
              <div className="text-[11px] text-slate-400">
                AWS 实例会先尝试 EC2 Instance Connect 临时密钥，失败再用密码 / 默认密钥。
              </div>
              <div className="flex gap-2 pt-1">
                <button className="btn flex-1" onClick={() => { setNeedPwd(false); connect(); }}>仅用密钥/EIC</button>
                <button className="btn-primary flex-1" onClick={() => connect(pwdInput || undefined)}>连接</button>
              </div>
            </div>
          </div>
        </div>
      )}

      <div ref={wrapRef} className="flex-1" style={{ background: "#0d0d12" }} />
    </div>
  );
}
