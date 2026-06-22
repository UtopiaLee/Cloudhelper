import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { useRef } from "react";
import { api } from "../lib/api";
import { PageHeader } from "../lib/components";
import { useToast } from "../lib/toast";

interface HealthCheck {
  name: string;
  ok: boolean;
  detail: string;
  duration_ms: number;
}
interface HealthReport {
  ok: boolean;
  started_at: number;
  uptime_sec: number;
  checks: HealthCheck[];
}
interface Job {
  id: string;
  name: string;
  trigger: string;
  next_run_time: string | null;
  func: string;
  misfire_grace_time: number;
}

interface TlsStatus {
  enabled: boolean;
  cert_path: string;
  key_path: string;
  subject: string;
  issuer: string;
  not_before: string | null;
  not_after: string | null;
  days_until_expiry: number | null;
  sans: string[];
  error: string;
}

export default function SystemPage() {
  const toast = useToast();
  const qc = useQueryClient();
  const health = useQuery({
    queryKey: ["health-full"],
    queryFn: async () => (await api.get<HealthReport>("/health/full", { params: { deep: true } })).data,
    refetchInterval: 15_000,
  });
  const jobs = useQuery({
    queryKey: ["jobs"],
    queryFn: async () => (await api.get<Job[]>("/system/jobs")).data,
    refetchInterval: 15_000,
  });
  const runJob = useMutation({
    mutationFn: async (id: string) => (await api.post(`/system/jobs/${id}/run-now`)).data,
    onSuccess: (_, id) => {
      toast.show(`已触发 ${id}`, "success");
      qc.invalidateQueries({ queryKey: ["jobs"] });
    },
  });

  const h = health.data;
  const uptime = h ? fmtUptime(h.uptime_sec) : "—";

  return (
    <div>
      <PageHeader
        title="系统状态"
        description={h ? `已运行 ${uptime}` : "加载中…"}
      />

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className="glass p-5">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold text-slate-900">健康检查</h3>
            {h?.ok ? <span className="tag tag-running">全部 OK</span> : <span className="tag tag-error">异常</span>}
          </div>
          {h?.checks.map((c) => (
            <div key={c.name} className={`flex items-start gap-3 px-3 py-2 rounded-lg mb-2 ${c.ok ? "bg-emerald-50/50" : "bg-red-50/50"}`}>
              <span className={`text-lg leading-none mt-0.5 ${c.ok ? "text-emerald-600" : "text-red-600"}`}>{c.ok ? "✓" : "✗"}</span>
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium text-slate-800">{c.name}</div>
                <div className="text-[11px] text-slate-500 font-mono break-all">{c.detail}</div>
              </div>
              <span className="text-[11px] text-slate-400 tabular-nums whitespace-nowrap">{c.duration_ms}ms</span>
            </div>
          ))}
        </motion.div>

        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 }} className="glass p-5">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold text-slate-900">后台任务</h3>
            <span className="text-xs text-slate-400">{jobs.data?.length || 0} 个</span>
          </div>
          {jobs.data?.map((j) => (
            <div key={j.id} className="flex items-center gap-3 px-3 py-2 rounded-lg bg-white/60 border border-white/50 mb-2">
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium text-slate-800 font-mono truncate">{j.id}</div>
                <div className="text-[11px] text-slate-500 truncate">{j.trigger}</div>
                <div className="text-[11px] text-slate-400">下次：{j.next_run_time ? new Date(j.next_run_time).toLocaleString() : "—"}</div>
              </div>
              <button className="btn btn-sm" disabled={runJob.isPending} onClick={() => runJob.mutate(j.id)}>立即跑</button>
            </div>
          ))}
          {jobs.data?.length === 0 && <div className="text-sm text-slate-400 py-3 text-center">无任务</div>}
        </motion.div>
      </div>

      <TLSSection />
    </div>
  );
}

function TLSSection() {
  const qc = useQueryClient();
  const toast = useToast();
  const fileCertRef = useRef<HTMLInputElement>(null);
  const fileKeyRef = useRef<HTMLInputElement>(null);

  const status = useQuery({
    queryKey: ["tls-status"],
    queryFn: async () => (await api.get<TlsStatus>("/system/tls")).data,
  });

  const upload = useMutation({
    mutationFn: async (v: { cert: File; key: File }) => {
      const form = new FormData();
      form.append("cert", v.cert);
      form.append("key", v.key);
      return (await api.post<TlsStatus>("/system/tls/upload", form, {
        headers: { "Content-Type": "multipart/form-data" },
      })).data;
    },
    onSuccess: (r) => {
      toast.show(`证书已上传，到期：${r.not_after ? new Date(r.not_after).toLocaleDateString() : "?"}`, "success");
      qc.invalidateQueries({ queryKey: ["tls-status"] });
    },
    onError: (e: Error) => toast.show(e.message, "error"),
  });

  const del = useMutation({
    mutationFn: async () => api.delete("/system/tls"),
    onSuccess: () => {
      toast.show("证书已删除", "success");
      qc.invalidateQueries({ queryKey: ["tls-status"] });
    },
  });

  async function doUpload() {
    const cert = fileCertRef.current?.files?.[0];
    const key = fileKeyRef.current?.files?.[0];
    if (!cert || !key) {
      toast.show("请同时选择证书和私钥文件", "error");
      return;
    }
    upload.mutate({ cert, key });
    if (fileCertRef.current) fileCertRef.current.value = "";
    if (fileKeyRef.current) fileKeyRef.current.value = "";
  }

  const s = status.data;
  const expireSoon = s?.days_until_expiry != null && s.days_until_expiry < 30;

  return (
    <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.2 }} className="glass p-5">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold text-slate-900">🔒 HTTPS 证书</h3>
        {s?.enabled ? <span className="tag tag-running">已启用</span> : <span className="tag tag-stopped">未配置</span>}
      </div>

      {s?.enabled ? (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3 text-xs">
            <Field label="域名">
              <div className="font-mono text-slate-700 break-all">{s.sans.length > 0 ? s.sans.join(", ") : "—"}</div>
            </Field>
            <Field label="到期">
              <div className={`font-semibold tabular-nums ${expireSoon ? "text-red-600" : "text-slate-700"}`}>
                {s.not_after ? new Date(s.not_after).toLocaleDateString() : "—"}
                {s.days_until_expiry != null && (
                  <span className="ml-2 text-[11px]">({s.days_until_expiry} 天后)</span>
                )}
              </div>
            </Field>
            <Field label="颁发者" wide>
              <div className="font-mono text-[11px] text-slate-600 break-all">{s.issuer}</div>
            </Field>
            <Field label="主体" wide>
              <div className="font-mono text-[11px] text-slate-600 break-all">{s.subject}</div>
            </Field>
          </div>

          {s.error && (
            <div className="text-xs text-red-600 bg-red-50/80 border border-red-200/60 rounded-md p-2">
              {s.error}
            </div>
          )}

          {expireSoon && (
            <div className="text-xs text-amber-700 bg-amber-50/80 border border-amber-200/60 rounded-md p-2">
              ⚠ 证书即将到期，建议更新
            </div>
          )}

          <div className="text-[11px] text-slate-500 bg-slate-50/60 rounded-md p-2.5">
            <b>注意</b>：证书已保存到 <code className="font-mono">data/ssl/</code>。
            实际生效需要 nginx 容器读到文件，请用 <code>docker compose -f docker-compose.yml -f docker-compose.tls.yml restart frontend</code>。
          </div>

          <div className="flex gap-2">
            <button className="btn" onClick={() => fileCertRef.current?.click()}>更换证书</button>
            <button className="btn-danger ml-auto" onClick={() => confirm("删除证书并恢复 HTTP 模式？") && del.mutate()}>
              删除证书
            </button>
          </div>
          <input ref={fileCertRef} type="file" accept=".pem,.crt,.cer" hidden onChange={() => fileKeyRef.current?.click()} />
          <input ref={fileKeyRef} type="file" accept=".pem,.key" hidden onChange={doUpload} />
        </div>
      ) : (
        <div className="space-y-3">
          <div className="text-xs text-slate-500 bg-slate-50/60 rounded-md p-3">
            上传 PEM 格式的证书和私钥即可启用 HTTPS。<br/>
            支持来源：Let's Encrypt (<code>certbot</code>)、Cloudflare Origin Certificate、自签证书、企业 CA。
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label">证书文件 (cert.pem / fullchain.pem)</label>
              <input ref={fileCertRef} type="file" accept=".pem,.crt,.cer" className="input text-xs" />
            </div>
            <div>
              <label className="label">私钥文件 (privkey.pem / key.pem)</label>
              <input ref={fileKeyRef} type="file" accept=".pem,.key" className="input text-xs" />
            </div>
          </div>

          <button
            className="btn-primary w-full"
            disabled={upload.isPending}
            onClick={doUpload}
          >
            {upload.isPending ? "上传中…" : "上传并启用"}
          </button>

          <div className="text-[11px] text-slate-400">
            私钥保留在本地 SQLite 数据目录下，不上传任何第三方
          </div>
        </div>
      )}
    </motion.div>
  );
}

function Field({ label, children, wide }: { label: string; children: React.ReactNode; wide?: boolean }) {
  return (
    <div className={wide ? "col-span-2" : ""}>
      <div className="text-[10px] uppercase tracking-widest text-slate-400 mb-1 font-semibold">{label}</div>
      <div>{children}</div>
    </div>
  );
}

function fmtUptime(s: number): string {
  if (s < 60) return `${s} 秒`;
  if (s < 3600) return `${Math.floor(s / 60)} 分钟`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时 ${Math.floor((s % 3600) / 60)} 分钟`;
  return `${Math.floor(s / 86400)} 天 ${Math.floor((s % 86400) / 3600)} 小时`;
}
