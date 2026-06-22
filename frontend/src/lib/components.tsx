import { ReactNode, useEffect, useState } from "react";
import { motion } from "framer-motion";

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className="flex items-start justify-between gap-4 mb-6"
    >
      <div>
        <h1 className="text-3xl font-bold tracking-tight gradient-text">{title}</h1>
        {description && <div className="text-sm text-slate-500 mt-1.5">{description}</div>}
      </div>
      {actions && <div className="flex gap-2 items-center flex-wrap">{actions}</div>}
    </motion.div>
  );
}

/** 通用 modal 外壳（玻璃 + 入场动画 + 标题栏 + 底部按钮） */
export function Modal({
  title, subtitle, width = 560, children, footer, onClose,
}: {
  title: string;
  subtitle?: ReactNode;
  width?: number;
  children: ReactNode;
  footer?: ReactNode;
  onClose: () => void;
}) {
  return (
    <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-sm flex items-center justify-center z-10 p-4">
      <motion.div
        initial={{ opacity: 0, scale: 0.95, y: 20 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        className="glass !p-0 max-h-[90vh] overflow-y-auto"
        style={{ width }}
      >
        <div className="px-6 py-4 border-b border-white/40 flex items-start justify-between">
          <div>
            <h3 className="text-lg font-bold gradient-text">{title}</h3>
            {subtitle && <div className="text-xs text-slate-500 mt-0.5">{subtitle}</div>}
          </div>
          <button className="btn-ghost btn-sm" onClick={onClose}>✕</button>
        </div>
        <div className="p-6 space-y-4">{children}</div>
        {footer && <div className="px-6 py-3 border-t border-white/40 flex justify-end gap-2">{footer}</div>}
      </motion.div>
    </div>
  );
}

/** 空态 / 加载态 / 错误态：统一灰色玻璃容器 */
export function Empty({ children }: { children: ReactNode }) {
  return <div className="glass p-10 text-center text-slate-400 text-sm">{children}</div>;
}

export function ErrorBox({ children }: { children: ReactNode }) {
  return (
    <div className="text-sm text-red-600 bg-red-50/80 border border-red-200/60 rounded-lg p-2.5">
      {children}
    </div>
  );
}

export function StateBadge({ state }: { state: string }) {
  const tagCls =
    state === "running" ? "tag tag-running"
      : state.startsWith("stop") ? "tag tag-stopped"
      : state === "pending" || state === "starting" ? "tag tag-pending"
      : "tag tag-error";
  const dotCls =
    state === "running" ? "status-dot-running"
      : state.startsWith("stop") ? "status-dot-stopped"
      : state === "pending" || state === "starting" ? "status-dot-pending"
      : "status-dot-error";
  return <span className={tagCls}><span className={`status-dot ${dotCls}`} />{state}</span>;
}

export function ProgressBar({ pct, height = 8 }: { pct: number; height?: number }) {
  const v = Math.min(100, Math.max(0, pct));
  const grad = v >= 100 ? "from-red-500 to-rose-500"
    : v >= 80 ? "from-amber-500 to-orange-500"
    : v >= 50 ? "from-yellow-400 to-amber-400"
    : "from-emerald-400 to-teal-500";
  return (
    <div className="relative w-full bg-slate-200/40 rounded-full overflow-hidden" style={{ height }}>
      <motion.div
        initial={{ width: 0 }}
        animate={{ width: `${v}%` }}
        transition={{ duration: 0.8, ease: "easeOut" }}
        className={`relative h-full rounded-full bg-gradient-to-r ${grad} progress-shimmer overflow-hidden`}
      />
    </div>
  );
}

export function fmtAgo(iso: string | null | undefined): string {
  if (!iso) return "—";
  const diffMs = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diffMs / 60000);
  if (m < 1) return "刚刚";
  if (m < 60) return `${m} 分钟前`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} 小时前`;
  return `${Math.floor(h / 24)} 天前`;
}

export function fmtBytes(g: number) {
  if (g < 0.01) return `${(g * 1024).toFixed(1)} MB`;
  return `${g.toFixed(2)} GB`;
}

const PROVIDER_GRADIENTS: Record<string, string> = {
  aws: "from-orange-500 to-amber-500",
  gcp: "from-blue-500 to-sky-500",
  oracle: "from-red-500 to-rose-500",
  azure: "from-sky-500 to-cyan-500",
};

export function ProviderTag({ provider }: { provider: string }) {
  const grad = PROVIDER_GRADIENTS[provider] || "from-slate-400 to-slate-500";
  return (
    <span className={`tag bg-gradient-to-r ${grad} text-white shadow-sm font-mono text-[10px] tracking-wider uppercase`}>
      {provider}
    </span>
  );
}

/** 数字滚动动画 */
export function AnimatedNumber({ value, decimals = 0, suffix = "" }: { value: number; decimals?: number; suffix?: string }) {
  const [display, setDisplay] = useState(0);
  useEffect(() => {
    let raf: number;
    const start = performance.now();
    const from = display;
    const to = value;
    const dur = 600;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / dur);
      const eased = 1 - Math.pow(1 - t, 3);
      setDisplay(from + (to - from) * eased);
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);
  return <>{display.toFixed(decimals)}{suffix}</>;
}
