import { createContext, useCallback, useContext, useState, ReactNode } from "react";
import { motion, AnimatePresence } from "framer-motion";

type Tone = "success" | "error" | "info";

interface Toast {
  id: number;
  tone: Tone;
  text: string;
}

interface ToastApi {
  show: (text: string, tone?: Tone) => void;
}

const Ctx = createContext<ToastApi>({ show: () => {} });

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const show = useCallback((text: string, tone: Tone = "info") => {
    const id = Date.now() + Math.random();
    setToasts((prev) => [...prev, { id, text, tone }]);
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 4000);
  }, []);
  return (
    <Ctx.Provider value={{ show }}>
      {children}
      <div className="fixed top-4 right-4 z-50 flex flex-col gap-2 pointer-events-none">
        <AnimatePresence>
          {toasts.map((t) => {
            const grad = t.tone === "success" ? "from-emerald-500 to-teal-500"
              : t.tone === "error" ? "from-red-500 to-rose-500"
              : "from-indigo-500 to-violet-500";
            const icon = t.tone === "success" ? "✓" : t.tone === "error" ? "✗" : "ℹ";
            return (
              <motion.div
                key={t.id}
                initial={{ opacity: 0, x: 80, scale: 0.95 }}
                animate={{ opacity: 1, x: 0, scale: 1 }}
                exit={{ opacity: 0, x: 80, scale: 0.95 }}
                className={`pointer-events-auto px-4 py-2.5 rounded-xl shadow-xl text-white bg-gradient-to-r ${grad} max-w-[400px] flex items-start gap-2`}
              >
                <span className="text-lg leading-none mt-0.5">{icon}</span>
                <span className="text-sm">{t.text}</span>
              </motion.div>
            );
          })}
        </AnimatePresence>
      </div>
    </Ctx.Provider>
  );
}

export function useToast() {
  return useContext(Ctx);
}

/** 给按钮加 spinner 状态 */
export function Spinner({ size = 14 }: { size?: number }) {
  return (
    <svg className="animate-spin" width={size} height={size} viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeOpacity="0.25" strokeWidth="3" />
      <path d="M12 2 A10 10 0 0 1 22 12" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}
