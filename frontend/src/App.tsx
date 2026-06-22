import { Routes, Route, Navigate, NavLink } from "react-router-dom";
import { motion } from "framer-motion";
import Dashboard from "./pages/Dashboard";
import AccountsPage from "./pages/Accounts";
import InstancesPage from "./pages/Instances";
import FirewallPage from "./pages/Firewall";
import SchedulesPage from "./pages/Schedules";
import SSHKeysPage from "./pages/SSHKeys";
import ShellPage from "./pages/Shell";
import SystemPage from "./pages/System";
import AuditPage from "./pages/Audit";
import { useAuthGuard, LoginPage } from "./lib/auth";
import { useKnockGate, KnockGate } from "./lib/knock";
import { api, setToken } from "./lib/api";

const tabs = [
  { to: "/dashboard", label: "总览", icon: "▤" },
  { to: "/accounts", label: "云账户", icon: "☰" },
  { to: "/instances", label: "实例", icon: "▢" },
  { to: "/shell", label: "终端", icon: "▶" },
  { to: "/firewall", label: "防火墙", icon: "◉" },
  { to: "/schedules", label: "定时任务", icon: "◷" },
  { to: "/ssh-keys", label: "SSH 密钥", icon: "🗝" },
  { to: "/system", label: "系统状态", icon: "⚙" },
  { to: "/audit", label: "审计", icon: "≡" },
];

export default function App() {
  const knock = useKnockGate();
  const auth = useAuthGuard();

  if (knock.state === "checking") {
    return <div className="min-h-screen flex items-center justify-center text-slate-400">加载中…</div>;
  }
  if (knock.state === "need-knock") {
    return <KnockGate onSuccess={knock.retry} />;
  }
  if (auth.state === "loading") {
    return <div className="min-h-screen flex items-center justify-center text-slate-400">加载中…</div>;
  }
  if (auth.state === "need-login") {
    return <LoginPage info={auth.info} onSuccess={auth.recheck} />;
  }
  return (
    <div className="min-h-screen flex">
      <aside className="w-60 shrink-0 m-4 mr-0 glass flex flex-col overflow-hidden">
        <div className="px-5 py-5 border-b border-white/40">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-indigo-500 via-violet-500 to-purple-600 flex items-center justify-center text-white text-lg font-bold shadow-lg shadow-violet-500/30">
              C
            </div>
            <div>
              <div className="font-bold tracking-tight text-slate-900 text-base">CloudHelper</div>
              <div className="text-[11px] text-slate-500 -mt-0.5">白嫖党管理面板</div>
            </div>
          </div>
        </div>
        <nav className="flex-1 p-3 flex flex-col gap-1">
          {tabs.map((t, i) => (
            <NavLink
              key={t.to}
              to={t.to}
              className={({ isActive }) =>
                `relative flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium transition-all duration-200 ${
                  isActive
                    ? "bg-gradient-to-r from-indigo-600 via-violet-600 to-purple-600 text-white shadow-lg shadow-violet-500/30"
                    : "text-slate-600 hover:bg-white/70 hover:text-slate-900"
                }`
              }
              style={{ animation: `float-up 0.3s ${i * 0.04}s backwards` }}
            >
              {({ isActive }) => (
                <>
                  <span className={`text-base ${isActive ? "" : "opacity-60"}`}>{t.icon}</span>
                  <span>{t.label}</span>
                  {isActive && (
                    <motion.span
                      layoutId="nav-indicator"
                      className="absolute right-3 w-1.5 h-1.5 rounded-full bg-white shadow-[0_0_8px_rgba(255,255,255,0.8)]"
                    />
                  )}
                </>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="px-3 py-2 border-t border-white/40">
          <button
            className="w-full text-xs text-slate-500 hover:text-slate-800 px-2 py-1.5 rounded hover:bg-white/40 transition-colors"
            onClick={async () => {
              try { await api.post("/auth/logout"); } catch (_) {}
              setToken("");
              window.dispatchEvent(new Event("ch-auth-changed"));
            }}
          >退出登录</button>
        </div>
        <div className="px-5 py-3 text-[11px] text-slate-400 border-t border-white/40 flex items-center justify-between">
          <span>v0.1.0</span>
          <span className="flex items-center gap-1">
            <span className="status-dot status-dot-running"></span>
            <span>运行中</span>
          </span>
        </div>
      </aside>

      <main className="flex-1 overflow-y-auto">
        <div className="max-w-7xl w-full mx-auto px-8 py-6">
          <Routes>
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/accounts" element={<AccountsPage />} />
            <Route path="/instances" element={<InstancesPage />} />
            <Route path="/shell" element={<ShellPage />} />
            <Route path="/firewall" element={<FirewallPage />} />
            <Route path="/schedules" element={<SchedulesPage />} />
            <Route path="/ssh-keys" element={<SSHKeysPage />} />
            <Route path="/system" element={<SystemPage />} />
            <Route path="/audit" element={<AuditPage />} />
          </Routes>
        </div>
      </main>
    </div>
  );
}
