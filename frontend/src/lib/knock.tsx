// KnockGate：前端首屏先确认 knock secret 有没有"敲对"，否则显示输入框。
// 比 LoginPage 更靠前的关卡。

import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { api, getKnock, setKnock } from "./api";

type State = "checking" | "ok" | "need-knock";

export function useKnockGate() {
  const [state, setState] = useState<State>("checking");
  const [hint, setHint] = useState("");

  async function probe() {
    try {
      // /health 不需要 knock，但 /auth/info 需要 —— 用它判断 knock 是否生效
      await api.get("/auth/info");
      setState("ok");
    } catch (e: any) {
      const msg = e?.message || "";
      if (msg.includes("Not Found") || msg.includes("404")) {
        setState("need-knock");
        setHint("访问路径未提供有效的密钥");
      } else {
        // 别的错误（后端没起 / 网络问题）当成 OK 让 App 走自己的错误处理
        setState("ok");
      }
    }
  }

  useEffect(() => {
    probe();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { state, hint, retry: probe };
}

export function KnockGate({ onSuccess }: { onSuccess: () => void }) {
  const [input, setInput] = useState(getKnock());
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function submit() {
    const v = input.trim();
    if (!v) {
      setErr("请输入访问密钥");
      return;
    }
    setBusy(true);
    setErr("");
    setKnock(v);
    try {
      await api.get("/auth/info");
      onSuccess();
    } catch (e: any) {
      setKnock("");
      setErr(e?.message?.includes("404") ? "密钥错误，请检查启动日志" : (e?.message || "验证失败"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-6">
      <motion.div
        initial={{ opacity: 0, y: 16, scale: 0.96 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        className="glass w-[460px] !p-0"
      >
        <div className="px-6 py-5 border-b border-white/40">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-amber-500 to-orange-600 flex items-center justify-center text-white text-lg shadow-lg shadow-orange-500/30">
              🔐
            </div>
            <div>
              <div className="text-lg font-bold gradient-text">访问受限</div>
              <div className="text-xs text-slate-500">此页面需要访问密钥</div>
            </div>
          </div>
        </div>

        <div className="p-6 space-y-3">
          <div className="text-xs text-slate-600 bg-amber-50/60 border border-amber-200/60 rounded-md p-3">
            后端启动时若未配置 KNOCK_SECRET 会在控制台打印当前 secret（仅显示末 4 位）。<br/>
            推荐在 <code className="font-mono text-[11px]">.env</code> 设置固定的 <code className="font-mono text-[11px]">KNOCK_SECRET</code>，<br/>
            然后把完整密钥粘贴到下方输入框。
          </div>

          <div>
            <label className="label">访问密钥 (Knock Secret)</label>
            <input
              autoFocus
              className="input font-mono"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
              placeholder="从后端控制台复制"
            />
          </div>

          {err && (
            <div className="text-xs text-red-600 bg-red-50/80 border border-red-200/60 rounded-md p-2">
              {err}
            </div>
          )}

          <button className="btn-primary w-full" disabled={busy} onClick={submit}>
            {busy ? "验证中…" : "继续"}
          </button>

          <div className="text-[11px] text-slate-400 text-center">
            密钥仅保存在当前标签页会话，关闭标签页后需重新输入；后端进程重启会换新密钥
          </div>
        </div>
      </motion.div>
    </div>
  );
}
