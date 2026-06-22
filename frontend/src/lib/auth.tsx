import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { api, getToken, setToken } from "./api";

interface AuthInfo {
  auth_required: boolean;
  username_auth: boolean;
  token_auth: boolean;
}

export function useAuthGuard() {
  const [state, setState] = useState<"loading" | "ok" | "need-login">("loading");
  const [info, setInfo] = useState<AuthInfo | null>(null);
  const [error, setError] = useState("");

  async function check() {
    try {
      const i = (await api.get<AuthInfo>("/auth/info")).data;
      setInfo(i);
      if (!i.auth_required) {
        setState("ok");
        return;
      }
      const t = getToken();
      if (!t) {
        setState("need-login");
        return;
      }
      try {
        await api.get("/accounts");
        setState("ok");
      } catch {
        setState("need-login");
      }
    } catch (e: any) {
      setError(e?.message || "无法连接后端");
      setState("loading");
    }
  }

  useEffect(() => {
    check();
    const onChange = () => check();
    window.addEventListener("ch-auth-changed", onChange);
    return () => window.removeEventListener("ch-auth-changed", onChange);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { state, info, error, recheck: check };
}

export function LoginPage({ info, onSuccess }: { info: AuthInfo | null; onSuccess: () => void }) {
  const [mode, setMode] = useState<"password" | "token">("password");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [token, setTok] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  // 自动选模式：只开了 username_auth 就用密码，只开了 token_auth 就用 token
  useEffect(() => {
    if (info?.username_auth && !info?.token_auth) setMode("password");
    else if (!info?.username_auth && info?.token_auth) setMode("token");
  }, [info]);

  async function submit() {
    setErr("");
    setBusy(true);
    try {
      const body: any = {};
      if (mode === "password") {
        if (!username || !password) throw new Error("请输入用户名和密码");
        body.username = username;
        body.password = password;
      } else {
        if (!token.trim()) throw new Error("请输入访问令牌");
        body.token = token.trim();
      }
      const resp = await api.post<{ ok: boolean; token: string }>("/auth/login", body);
      if (resp.data.token) setToken(resp.data.token);
      onSuccess();
    } catch (e: any) {
      setErr(e?.message || "登录失败");
    } finally {
      setBusy(false);
    }
  }

  const showTabs = info?.username_auth && info?.token_auth;

  return (
    <div className="min-h-screen flex items-center justify-center p-6">
      <motion.div
        initial={{ opacity: 0, y: 16, scale: 0.96 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        className="glass w-[400px] !p-0"
      >
        <div className="px-6 py-5 border-b border-white/40">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-indigo-500 via-violet-500 to-purple-600 flex items-center justify-center text-white text-lg font-bold shadow-lg shadow-violet-500/30">C</div>
            <div>
              <div className="text-lg font-bold gradient-text">CloudHelper 登录</div>
              <div className="text-xs text-slate-500">
                {mode === "password" ? "账号 + 密码登录" : "访问令牌登录"}
              </div>
            </div>
          </div>
        </div>
        <div className="p-6 space-y-3">
          {showTabs && (
            <div className="flex rounded-lg border border-white/50 bg-white/60 p-0.5 text-xs">
              <button
                className={`flex-1 px-3 py-1.5 rounded-md transition-all ${mode === "password" ? "bg-gradient-to-r from-indigo-600 to-violet-600 text-white shadow-sm" : "text-slate-600"}`}
                onClick={() => setMode("password")}
              >账号密码</button>
              <button
                className={`flex-1 px-3 py-1.5 rounded-md transition-all ${mode === "token" ? "bg-gradient-to-r from-indigo-600 to-violet-600 text-white shadow-sm" : "text-slate-600"}`}
                onClick={() => setMode("token")}
              >访问令牌</button>
            </div>
          )}

          {mode === "password" ? (
            <>
              <div>
                <label className="label">用户名</label>
                <input autoFocus className="input" value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") document.getElementById("ch-pwd")?.focus(); }} />
              </div>
              <div>
                <label className="label">密码</label>
                <input id="ch-pwd" type="password" className="input" value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") submit(); }} />
              </div>
            </>
          ) : (
            <div>
              <label className="label">Access Token</label>
              <input type="password" autoFocus className="input font-mono"
                value={token} onChange={(e) => setTok(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
                placeholder=".env 里 ACCESS_TOKEN 的值" />
            </div>
          )}

          {err && <div className="text-xs text-red-600 bg-red-50/80 border border-red-200/60 rounded-md p-2">{err}</div>}
          <button className="btn-primary w-full" disabled={busy} onClick={submit}>
            {busy ? "验证中…" : "登录"}
          </button>
          <div className="text-[11px] text-slate-400 text-center">
            登录信息保留 30 天
          </div>
        </div>
      </motion.div>
    </div>
  );
}

