import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import { useNavigate } from "react-router-dom";
import { api, Account, BulkResult, Instance, Schedule } from "../lib/api";
import { useAccount } from "../lib/account-context";
import { PageHeader, ProgressBar, ProviderTag, StateBadge, fmtAgo, fmtBytes } from "../lib/components";
import { Spinner, useToast } from "../lib/toast";
import { AWS_IMAGE_ALIASES, AZURE_IMAGES, DEFAULT_TRAFFIC_GB, DISK_SIZE_PRESETS, DISK_TYPES, GCP_IMAGES, INSTANCE_TYPES, ORACLE_IMAGES, REGIONS } from "../lib/presets";
import { TrafficChart } from "../lib/charts";

type Scope = "all" | "current" | "group";
type View = "card" | "table";

export default function InstancesPage() {
  const { current, group, groups, setGroup, accounts } = useAccount();
  const qc = useQueryClient();
  const toast = useToast();
  const [scope, setScope] = useState<Scope>("all");
  const [view, setView] = useState<View>("card");
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [search, setSearch] = useState("");
  const [stateFilter, setStateFilter] = useState("");
  const [openCreate, setOpenCreate] = useState(false);
  const [scheduleFor, setScheduleFor] = useState<Instance | null>(null);

  const list = useQuery({
    enabled: scope === "current" ? !!current : true,
    queryKey: ["instances", scope, current?.id, group],
    queryFn: async () => {
      if (scope === "current" && current) {
        return (await api.get<Instance[]>(`/accounts/${current.id}/instances`)).data;
      }
      const params: Record<string, string> = {};
      if (scope === "group" && group) params.group = group;
      return (await api.get<Instance[]>("/fleet/instances", { params })).data;
    },
  });

  const refresh = useMutation({
    mutationFn: async () => {
      if (scope === "current" && current) {
        return (await api.get<Instance[]>(`/accounts/${current.id}/instances`, { params: { refresh: true } })).data;
      }
      const params: Record<string, string | boolean> = { refresh: true };
      if (scope === "group" && group) params.group = group;
      return (await api.get<Instance[]>("/fleet/instances", { params })).data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["instances"] }),
  });

  const filtered = useMemo(() => {
    const items = list.data || [];
    const s = search.toLowerCase();
    return items.filter((i) =>
      (!s || i.id.includes(s) || i.name.toLowerCase().includes(s) || i.public_ip.includes(s)) &&
      (!stateFilter || i.state === stateFilter)
    );
  }, [list.data, search, stateFilter]);

  const bulkAction = useMutation({
    mutationFn: async (v: { action: "start" | "stop" | "restart" | "terminate" | "set-limit"; traffic_limit_gb?: number }) => {
      const targets = Object.entries(selected)
        .filter(([, x]) => x)
        .map(([k]) => {
          const inst = filtered.find((i) => keyOf(i) === k)!;
          return { account_id: String(inst.account_id), instance_id: inst.id, region: inst.region, zone: inst.zone };
        });
      const body: any = { action: v.action, targets };
      if (v.traffic_limit_gb !== undefined) body.traffic_limit_gb = v.traffic_limit_gb;
      return (await api.post<BulkResult>("/fleet/bulk", body)).data;
    },
    onSuccess: (r) => {
      toast.show(`完成 ${r.ok}/${r.total}${r.failed ? `，失败 ${r.failed}` : ""}`, r.failed ? "error" : "success");
      setSelected({});
      qc.invalidateQueries({ queryKey: ["instances"] });
    },
    onError: (e: Error) => toast.show(e.message, "error"),
  });

  const setLimit = useMutation({
    mutationFn: async (v: { account_id: number; id: string; gb: number }) =>
      api.put(`/accounts/${v.account_id}/instances/${v.id}/traffic-limit`, { traffic_limit_gb: v.gb }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["instances"] }),
  });
  const setSSH = useMutation({
    mutationFn: async (v: { account_id: number; id: string; ssh_user: string; ssh_port: number }) =>
      api.put(`/accounts/${v.account_id}/instances/${v.id}/ssh`, { ssh_user: v.ssh_user, ssh_port: v.ssh_port }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["instances"] }),
  });
  const collect = useMutation({
    mutationFn: async (i: Instance) =>
      (await api.post(`/accounts/${i.account_id}/instances/${i.id}/collect`)).data,
    onSuccess: (r: any) => {
      if (r.ok) toast.show(`采集成功：本月 ${((r.bytes_in + r.bytes_out) / 1e6).toFixed(2)} MB · CPU ${(r.cpu_pct||0).toFixed(0)}% · MEM ${(r.mem_pct||0).toFixed(0)}%`, "success");
      else toast.show(`采集失败：${r.error}`, "error");
      qc.invalidateQueries({ queryKey: ["instances"] });
    },
    onError: (e: Error) => toast.show(e.message, "error"),
  });
  const rotateIp = useMutation({
    mutationFn: async (i: Instance) =>
      (await api.post(`/accounts/${i.account_id}/instances/${i.id}/rotate-ip`,
        null, { params: { region: i.region, zone: i.zone } })).data,
    onMutate: (i: Instance) => toast.show(`正在切换 ${i.name || i.id} 的 IP，可能需要 30-60 秒…`, "info"),
    onSuccess: (r: any, i: Instance) => {
      toast.show(`新 IP：${r.new_ip || "未获取"}`, "success");
      qc.invalidateQueries({ queryKey: ["instances"] });
    },
    onError: (e: Error) => toast.show(`切换失败：${e.message}`, "error"),
  });
  const singleAction = useMutation({
    mutationFn: async (v: { kind: "start" | "stop" | "terminate" | "force-start"; inst: Instance }) => {
      const params = { region: v.inst.region, zone: v.inst.zone };
      if (v.kind === "terminate") {
        await api.delete(`/accounts/${v.inst.account_id}/instances/${v.inst.id}`, { params });
      } else if (v.kind === "force-start") {
        await api.post(`/accounts/${v.inst.account_id}/instances/${v.inst.id}/force-start`, null, { params });
      } else {
        await api.post(`/accounts/${v.inst.account_id}/instances/${v.inst.id}/${v.kind}`, null, { params });
      }
      return v;
    },
    onMutate: (v) => {
      const labels: Record<string, string> = { start: "启动", stop: "停止", terminate: "销毁", "force-start": "强制启动" };
      toast.show(`正在${labels[v.kind]} ${v.inst.name || v.inst.id}…`, "info");
    },
    onSuccess: (v) => {
      const labels: Record<string, string> = { start: "已启动", stop: "已停止", terminate: "已销毁", "force-start": "已强启" };
      toast.show(`${labels[v.kind]} ${v.inst.name || v.inst.id}`, "success");
      qc.invalidateQueries({ queryKey: ["instances"] });
    },
    onError: (e: Error) => toast.show(e.message, "error"),
  });

  const selectedCount = Object.values(selected).filter(Boolean).length;
  const navigate = useNavigate();
  const [diagnoseFor, setDiagnoseFor] = useState<Instance | null>(null);
  const [chartFor, setChartFor] = useState<Instance | null>(null);
  const handlers = { setLimit, setSSH, collect, singleAction, rotateIp,
                     openSchedule: setScheduleFor,
                     openShell: (i: Instance) => navigate(`/shell?open=${i.account_id}:${i.id}`),
                     openDiagnose: (i: Instance) => setDiagnoseFor(i),
                     openChart: (i: Instance) => setChartFor(i) };

  return (
    <div>
      <PageHeader
        title="实例"
        description={`${filtered.length} 台实例`}
        actions={
          <>
            <div className="flex rounded-lg border border-white/50 bg-white/60 backdrop-blur p-0.5 text-xs">
              <button className={`px-3 py-1 rounded-md transition-all ${view === "card" ? "bg-gradient-to-r from-indigo-600 to-violet-600 text-white shadow-sm" : "text-slate-600 hover:text-slate-900"}`}
                onClick={() => setView("card")}>卡片</button>
              <button className={`px-3 py-1 rounded-md transition-all ${view === "table" ? "bg-gradient-to-r from-indigo-600 to-violet-600 text-white shadow-sm" : "text-slate-600 hover:text-slate-900"}`}
                onClick={() => setView("table")}>表格</button>
            </div>
            <select className="input w-32" value={scope} onChange={(e) => setScope(e.target.value as Scope)}>
              <option value="all">全部账户</option>
              <option value="group">分组</option>
              <option value="current">当前账户</option>
            </select>
            {scope === "group" && (
              <select className="input w-32" value={group ?? ""} onChange={(e) => setGroup(e.target.value || null)}>
                <option value="">全部分组</option>
                {groups.map((g) => <option key={g} value={g}>{g}</option>)}
              </select>
            )}
            <input className="input w-40" placeholder="搜索 ID / IP / 名称" value={search} onChange={(e) => setSearch(e.target.value)} />
            <select className="input w-28" value={stateFilter} onChange={(e) => setStateFilter(e.target.value)}>
              <option value="">所有状态</option>
              <option value="running">running</option>
              <option value="stopped">stopped</option>
              <option value="pending">pending</option>
            </select>
            <button className="btn" disabled={refresh.isPending} onClick={() => refresh.mutate()}>
              {refresh.isPending ? "刷新中…" : "↻ 刷新"}
            </button>
            <button className="btn-primary" disabled={accounts.length === 0} onClick={() => setOpenCreate(true)}>+ 创建</button>
          </>
        }
      />

      <AnimatePresence>
        {selectedCount > 0 && (
          <motion.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            className="mb-4 px-4 py-2.5 rounded-xl bg-gradient-to-r from-indigo-600 via-violet-600 to-purple-600 text-white shadow-lg shadow-violet-500/30 flex items-center gap-2 flex-wrap"
          >
            <span className="text-sm font-medium">已选 {selectedCount} 个实例</span>
            <button className="btn btn-sm" onClick={() => bulkAction.mutate({ action: "start" })}>启动</button>
            <button className="btn btn-sm" onClick={() => bulkAction.mutate({ action: "stop" })}>停止</button>
            <button className="btn btn-sm" onClick={() => confirm("批量重启？") && bulkAction.mutate({ action: "restart" })}>重启</button>
            <button className="btn btn-sm" onClick={() => {
              const v = prompt(`将 ${selectedCount} 个实例的流量上限统一改为多少 GB？`);
              if (!v) return;
              const n = Number(v);
              if (isNaN(n) || n < 0) { toast.show("无效数字", "error"); return; }
              bulkAction.mutate({ action: "set-limit", traffic_limit_gb: n });
            }}>改限额</button>
            <button className="btn-danger btn-sm" onClick={() => {
              if (confirm(`确认销毁 ${selectedCount} 个实例？\n\n此操作不可恢复！`))
                bulkAction.mutate({ action: "terminate" });
            }}>✕ 销毁</button>
            <button className="btn btn-sm ml-auto" onClick={() => setSelected({})}>取消</button>
          </motion.div>
        )}
      </AnimatePresence>

      {list.isLoading ? (
        <div className="glass p-12 text-center text-slate-400">加载中…</div>
      ) : filtered.length === 0 ? (
        <div className="glass p-12 text-center text-slate-400">无数据，点 ↻ 从云刷新</div>
      ) : view === "card" ? (
        <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-5">
          {filtered.map((i, idx) => (
            <InstanceCard key={keyOf(i)} idx={idx} inst={i}
              selected={!!selected[keyOf(i)]}
              onSelect={(v) => setSelected({ ...selected, [keyOf(i)]: v })}
              handlers={handlers} />
          ))}
        </div>
      ) : (
        <InstanceTable items={filtered} selected={selected} setSelected={setSelected} handlers={handlers} />
      )}

      {openCreate && (
        <CreateInstanceModal
          accounts={accounts}
          defaultAccountId={current?.id}
          onClose={() => { setOpenCreate(false); qc.invalidateQueries({ queryKey: ["instances"] }); }}
        />
      )}
      {scheduleFor && (
        <ScheduleModal
          inst={scheduleFor}
          onClose={() => setScheduleFor(null)}
        />
      )}
      {diagnoseFor && (
        <DiagnoseModal inst={diagnoseFor} onClose={() => setDiagnoseFor(null)} />
      )}
      {chartFor && (
        <TrafficChartModal inst={chartFor} onClose={() => setChartFor(null)} />
      )}
    </div>
  );
}

function keyOf(i: Instance) {
  return `${i.account_id}:${i.id}`;
}

function InstanceCard({ inst: i, selected, onSelect, handlers, idx }: {
  inst: Instance; selected: boolean; onSelect: (v: boolean) => void; handlers: any; idx: number;
}) {
  const { setLimit, setSSH, collect, singleAction, openSchedule, openShell, rotateIp } = handlers;
  const isRunning = i.state === "running";
  // 卡片是否有正在进行的耗时操作
  const isBusy = singleAction.isPending && singleAction.variables?.inst.id === i.id ||
                 rotateIp.isPending && rotateIp.variables?.id === i.id ||
                 collect.isPending && collect.variables?.id === i.id;
  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: Math.min(idx * 0.04, 0.4) }}
      whileHover={{ y: -4 }}
      className={`glass !p-0 overflow-hidden transition-all duration-300 relative ${
        selected
          ? "ring-2 ring-indigo-500 shadow-[0_15px_45px_-15px_rgba(99,102,241,0.5)]"
          : "hover:shadow-[0_20px_50px_-12px_rgba(99,102,241,0.35)]"
      }`}
    >
      {/* 加载遮罩 */}
      {isBusy && (
        <div className="absolute inset-0 z-20 bg-white/60 backdrop-blur-sm flex items-center justify-center pointer-events-auto">
          <div className="flex items-center gap-2 px-4 py-2 rounded-xl bg-gradient-to-r from-indigo-600 to-violet-600 text-white shadow-xl">
            <Spinner size={18} />
            <span className="text-sm font-medium">操作进行中…</span>
          </div>
        </div>
      )}
      {/* 渐变顶部条 */}
      <div className={`h-1 bg-gradient-to-r ${
        isRunning ? "from-emerald-400 via-teal-400 to-cyan-400"
        : i.state.startsWith("stop") ? "from-slate-300 to-slate-400"
        : i.state === "pending" ? "from-amber-400 to-orange-400"
        : "from-red-400 to-rose-400"
      }`} />

      <div className="px-5 py-3.5 border-b border-white/40 flex items-center gap-2.5">
        <input type="checkbox" checked={selected} onChange={(e) => onSelect(e.target.checked)}
          className="w-4 h-4 rounded text-indigo-600 focus:ring-2 focus:ring-indigo-400/40" />
        <ProviderTag provider={i.account_provider} />
        <div className="min-w-0 flex-1">
          <div className="font-bold text-slate-900 truncate">{i.name || i.id}</div>
          <div className="text-[11px] text-slate-500 font-mono truncate">{i.account_name}</div>
        </div>
        <StateBadge state={i.state} />
      </div>

      <div className="px-5 py-4 grid grid-cols-2 gap-x-4 gap-y-3 text-xs">
        <Field label="规格"><span className="font-mono font-semibold text-slate-800">{i.instance_type || "—"}</span>{i.arch && <span className="ml-1 text-slate-400">({i.arch})</span>}</Field>
        <Field label="区域"><span className="font-mono">{i.region}</span>{i.zone && <span className="text-slate-400"> / {i.zone.replace(i.region + "-", "")}</span>}</Field>
        <Field label="单价">
          {i.hourly_usd > 0
            ? <span className="tabular-nums">${i.hourly_usd.toFixed(4)}<span className="text-slate-400">/h</span></span>
            : <span className="text-emerald-600 font-medium">免费</span>}
        </Field>
        <Field label="日预算">
          {i.daily_usd > 0
            ? <span className="tabular-nums font-semibold">${i.daily_usd.toFixed(2)}<span className="text-slate-400">/天</span></span>
            : <span className="text-slate-400">—</span>}
        </Field>
        <Field label="磁盘">{i.disk_gb ? <span className="font-semibold text-slate-800">{i.disk_gb} GB</span> : "—"}</Field>
        <Field label="创建时间">{i.launched_at ? new Date(i.launched_at).toLocaleDateString() : "—"}</Field>
        <Field label="公网 IP"><span className="font-mono text-slate-800">{i.public_ip || <span className="text-slate-400">无</span>}</span></Field>
        <Field label="内网 IP"><span className="font-mono">{i.private_ip || "—"}</span></Field>
        <Field label="镜像" wide><span className="font-mono text-[11px] text-slate-600 truncate block" title={i.image}>{i.image || "—"}</span></Field>
        <Field label="安全组" wide>
          {i.security_groups.length ? (
            <div className="flex flex-wrap gap-1">
              {i.security_groups.map((sg) => <span key={sg} className="font-mono text-[11px] bg-slate-100/80 rounded px-1.5 py-0.5">{sg}</span>)}
            </div>
          ) : "—"}
        </Field>
        <Field label="实例 ID" wide><span className="font-mono text-[11px] text-slate-500 truncate block" title={i.id}>{i.id}</span></Field>
      </div>

      <div className="px-5 py-3.5 border-t border-white/40 bg-gradient-to-br from-indigo-50/40 to-purple-50/30">
        {/* CPU + 内存 mini bar */}
        {isRunning && (i.cpu_pct > 0 || i.mem_pct > 0) && (
          <div className="grid grid-cols-2 gap-3 mb-3">
            <div>
              <div className="flex items-center justify-between text-[11px] mb-1">
                <span className="font-semibold text-slate-600 uppercase tracking-wider">CPU</span>
                <span className={`tabular-nums font-bold ${i.cpu_pct >= 80 ? "text-red-600" : i.cpu_pct >= 50 ? "text-amber-600" : "text-emerald-600"}`}>{i.cpu_pct.toFixed(0)}%</span>
              </div>
              <ProgressBar pct={i.cpu_pct} height={4} />
              <div className="text-[10px] text-slate-400 mt-0.5">load {i.load1.toFixed(2)} / {i.load5.toFixed(2)}</div>
            </div>
            <div>
              <div className="flex items-center justify-between text-[11px] mb-1">
                <span className="font-semibold text-slate-600 uppercase tracking-wider">MEM</span>
                <span className={`tabular-nums font-bold ${i.mem_pct >= 80 ? "text-red-600" : i.mem_pct >= 50 ? "text-amber-600" : "text-emerald-600"}`}>{i.mem_pct.toFixed(0)}%</span>
              </div>
              <ProgressBar pct={i.mem_pct} height={4} />
              <div className="text-[10px] text-slate-400 mt-0.5">{i.mem_used_mb} / {i.mem_total_mb} MB</div>
            </div>
          </div>
        )}

        <div className="flex items-center justify-between mb-2">
          <div className="text-[11px] font-semibold text-slate-600 uppercase tracking-wider">
            本月出站流量
            {i.iface && <span className="ml-1 font-mono text-slate-400 normal-case tracking-normal">({i.iface})</span>}
          </div>
          <div className="text-xs text-slate-700 tabular-nums font-medium">
            {fmtBytes(i.monthly_traffic_out_gb)} <span className="text-slate-400">/ {i.traffic_limit_gb.toFixed(1)} GB</span>
            <span className={`ml-1.5 font-bold ${i.monthly_traffic_pct >= 90 ? "text-red-600" : i.monthly_traffic_pct >= 80 ? "text-amber-600" : i.monthly_traffic_pct >= 50 ? "text-yellow-600" : "text-emerald-600"}`}>
              {i.monthly_traffic_pct.toFixed(0)}%
            </span>
          </div>
        </div>
        <ProgressBar pct={i.monthly_traffic_pct} />
        <div className="text-[11px] text-slate-400 mt-1.5">
          入站 {fmtBytes(i.monthly_traffic_gb - i.monthly_traffic_out_gb)} <span className="text-slate-300">·</span> 不计入阈值
          {i.auto_stopped_by_traffic && (
            <span className="ml-2 text-red-600 font-medium">⚠ 已被自动关机（90% 阈值），月初恢复或手动强启</span>
          )}
        </div>

        <div className="flex items-center gap-2 mt-3 text-[11px] text-slate-500">
          <span className="font-medium">SSH</span>
          <input className="input !py-0.5 !px-2 w-20 text-xs" placeholder="user"
            defaultValue={i.ssh_user}
            onBlur={(e) => { if (e.target.value !== i.ssh_user) setSSH.mutate({ account_id: i.account_id, id: i.id, ssh_user: e.target.value, ssh_port: i.ssh_port || 22 }); }} />
          <span>:</span>
          <input className="input !py-0.5 !px-2 w-14 text-xs" type="number"
            defaultValue={i.ssh_port || 22}
            onBlur={(e) => { const p = Number(e.target.value); if (p !== i.ssh_port) setSSH.mutate({ account_id: i.account_id, id: i.id, ssh_user: i.ssh_user, ssh_port: p }); }} />
          <span className="ml-auto text-slate-500">活跃 <span className="text-slate-700 font-medium">{fmtAgo(i.last_alive_at)}</span></span>
        </div>
        {i.last_collect_error && (
          <div className="mt-2 text-[11px] text-red-600 bg-red-50/50 rounded px-2 py-1 truncate" title={i.last_collect_error}>
            ⚠ {i.last_collect_error}
          </div>
        )}
        <div className="flex items-center gap-2 mt-2 text-[11px] text-slate-500">
          <span className="font-medium">流量上限</span>
          <input type="number" min={0} step={0.5} defaultValue={i.traffic_limit_gb}
            className="input !py-0.5 !px-2 w-16 text-xs"
            onBlur={(e) => { const v = Number(e.target.value); if (v !== i.traffic_limit_gb) setLimit.mutate({ account_id: i.account_id, id: i.id, gb: v }); }} />
          <span className="text-slate-400">GB / 月</span>
        </div>
      </div>

      <div className="px-5 py-3 border-t border-white/40 flex gap-1.5 items-center">
        <button className="btn btn-sm" disabled={isRunning} onClick={() => singleAction.mutate({ kind: "start", inst: i })}>▶ 启动</button>
        <button className="btn btn-sm" disabled={!isRunning} onClick={() => singleAction.mutate({ kind: "stop", inst: i })}>■ 停止</button>
        <button className="btn-primary btn-sm" disabled={!isRunning || !i.public_ip} onClick={() => openShell(i)}>💻 Shell</button>
        <CardMoreMenu inst={i} handlers={handlers} />
        <button className="btn-danger btn-sm ml-auto" onClick={() => confirm(`销毁 ${i.id}？`) && singleAction.mutate({ kind: "terminate", inst: i })}>✕ 销毁</button>
      </div>
    </motion.div>
  );
}

function Field({ label, children, wide }: { label: string; children: React.ReactNode; wide?: boolean }) {
  return (
    <div className={wide ? "col-span-2" : ""}>
      <div className="text-[10px] uppercase tracking-widest text-slate-400 mb-1 font-semibold">{label}</div>
      <div className="text-slate-700">{children}</div>
    </div>
  );
}

function CardMoreMenu({ inst: i, handlers }: { inst: Instance; handlers: any }) {
  const { collect, singleAction, openSchedule, openDiagnose, rotateIp, openChart } = handlers;
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  return (
    <div className="relative" ref={wrapRef}>
      <button className="btn btn-sm" onClick={() => setOpen((v) => !v)}>⋯</button>
      {open && (
        <div className="absolute left-0 bottom-full mb-1 z-30 glass !p-1 min-w-[180px] shadow-xl">
          <MenuItem onClick={() => { setOpen(false); collect.mutate(i); }}>⟳ 立即采集</MenuItem>
          <MenuItem onClick={() => { setOpen(false); openChart(i); }}>📈 流量曲线</MenuItem>
          <MenuItem onClick={() => { setOpen(false); openSchedule(i); }}>⏱ 定时任务</MenuItem>
          {openDiagnose && (
            <MenuItem onClick={() => { setOpen(false); openDiagnose(i); }}>🩺 诊断连接</MenuItem>
          )}
          <MenuItem onClick={() => {
            setOpen(false);
            if (confirm(`切换 ${i.name || i.id} 的公网 IP？\n\nAWS 实例可能需要重启，约 30-60 秒。`))
              rotateIp.mutate(i);
          }}>🔄 切换 IP</MenuItem>
          {i.auto_stopped_by_traffic && (
            <MenuItem
              warn
              onClick={() => {
                setOpen(false);
                if (confirm("已超流量阈值，强制启动可能产生费用！确认？"))
                  singleAction.mutate({ kind: "force-start", inst: i });
              }}
            >⚡ 强制启动</MenuItem>
          )}
        </div>
      )}
    </div>
  );
}

function MenuItem({ onClick, children, warn }: { onClick: () => void; children: React.ReactNode; warn?: boolean }) {
  return (
    <button
      className={`w-full text-left px-3 py-1.5 rounded-md text-sm transition-colors ${warn ? "text-amber-700 hover:bg-amber-100 font-medium" : "text-slate-700 hover:bg-slate-100"}`}
      onClick={onClick}
    >{children}</button>
  );
}

function TrafficChartModal({ inst, onClose }: { inst: Instance; onClose: () => void }) {
  const [days, setDays] = useState(7);
  const q = useQuery({
    queryKey: ["traffic-history", inst.account_id, inst.id, days],
    queryFn: async () => (await api.get<any[]>(
      `/accounts/${inst.account_id}/instances/${inst.id}/traffic-history`,
      { params: { days } }
    )).data,
  });
  return (
    <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-sm flex items-center justify-center z-10 p-4">
      <motion.div
        initial={{ opacity: 0, scale: 0.95, y: 20 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        className="glass w-[820px] max-h-[90vh] overflow-y-auto !p-0"
      >
        <div className="px-6 py-4 border-b border-white/40 flex items-start justify-between">
          <div>
            <h3 className="text-lg font-bold gradient-text">📈 流量历史</h3>
            <div className="text-xs text-slate-500 mt-0.5 font-mono">{inst.name || inst.id}</div>
          </div>
          <div className="flex items-center gap-2">
            {[1, 7, 30, 90].map((d) => (
              <button key={d}
                className={`px-2.5 py-1 rounded text-xs ${days === d ? "bg-indigo-600 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"}`}
                onClick={() => setDays(d)}>近 {d} 天</button>
            ))}
            <button className="btn-ghost btn-sm" onClick={onClose}>✕</button>
          </div>
        </div>
        <div className="p-6">
          {q.isLoading && <div className="text-sm text-slate-400 py-12 text-center">加载中…</div>}
          {q.isError && <div className="text-sm text-red-600 py-6 text-center">{(q.error as Error).message}</div>}
          {q.data && <TrafficChart data={q.data} />}
          {q.data && q.data.length > 0 && (
            <div className="grid grid-cols-3 gap-3 mt-4 text-xs">
              <div className="rounded-lg bg-white/60 border border-white/50 p-3">
                <div className="text-slate-500">采样点数</div>
                <div className="text-lg font-semibold tabular-nums mt-1">{q.data.length}</div>
              </div>
              <div className="rounded-lg bg-white/60 border border-white/50 p-3">
                <div className="text-slate-500">区间出站</div>
                <div className="text-lg font-semibold tabular-nums mt-1">
                  {q.data.length > 1
                    ? fmtBytes(Math.max(0, q.data[q.data.length - 1].bytes_out - q.data[0].bytes_out) / (1024 ** 3))
                    : "—"}
                </div>
              </div>
              <div className="rounded-lg bg-white/60 border border-white/50 p-3">
                <div className="text-slate-500">区间入站</div>
                <div className="text-lg font-semibold tabular-nums mt-1">
                  {q.data.length > 1
                    ? fmtBytes(Math.max(0, q.data[q.data.length - 1].bytes_in - q.data[0].bytes_in) / (1024 ** 3))
                    : "—"}
                </div>
              </div>
            </div>
          )}
          {q.data && q.data.length === 0 && (
            <div className="text-sm text-slate-400 py-12 text-center">
              暂无采样数据。等下一轮 SSH 采集（10 分钟）后再回来看。
            </div>
          )}
        </div>
      </motion.div>
    </div>
  );
}

function DiagnoseModal({ inst, onClose }: { inst: Instance; onClose: () => void }) {
  const run = useMutation({
    mutationFn: async () => (await api.post(`/accounts/${inst.account_id}/instances/${inst.id}/diagnose`)).data,
  });
  const fix = useMutation({
    mutationFn: async () => (await api.post(`/accounts/${inst.account_id}/instances/${inst.id}/ensure-ssh-firewall`)).data,
    onSuccess: () => run.mutate(),
  });
  useEffect(() => { run.mutate(); /* eslint-disable-next-line */ }, []);

  const r = run.data as any;
  const checks: any[] = r?.checks || [];
  const failedFw = checks.find((c) => c.name?.includes("防火墙含 22") && !c.ok);

  return (
    <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-sm flex items-center justify-center z-10 p-4">
      <motion.div
        initial={{ opacity: 0, scale: 0.95, y: 20 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        className="glass w-[640px] max-h-[90vh] overflow-y-auto !p-0"
      >
        <div className="px-6 py-4 border-b border-white/40 flex items-start justify-between">
          <div>
            <h3 className="text-lg font-bold gradient-text">🩺 实例诊断</h3>
            <div className="text-xs text-slate-500 mt-0.5 font-mono">
              {inst.ssh_user || "root"}@{inst.public_ip}:{inst.ssh_port || 22}
            </div>
          </div>
          <button className="btn-ghost btn-sm" onClick={onClose}>✕</button>
        </div>

        <div className="p-6 space-y-3">
          {run.isPending && <div className="text-slate-500 text-sm">检查中…</div>}

          {checks.map((c, idx) => (
            <div key={idx} className={`flex items-start gap-3 p-3 rounded-lg border ${c.ok ? "bg-emerald-50/50 border-emerald-200/50" : "bg-red-50/50 border-red-200/50"}`}>
              <div className={`text-lg leading-none mt-0.5 ${c.ok ? "text-emerald-600" : "text-red-600"}`}>{c.ok ? "✓" : "✗"}</div>
              <div className="flex-1 min-w-0">
                <div className={`text-sm font-medium ${c.ok ? "text-emerald-900" : "text-red-900"}`}>{c.name}</div>
                <div className="text-xs text-slate-600 mt-1 font-mono whitespace-pre-wrap break-all">{c.detail}</div>
              </div>
            </div>
          ))}

          {failedFw && (
            <button className="btn-primary w-full" disabled={fix.isPending} onClick={() => fix.mutate()}>
              {fix.isPending ? "添加中…" : "🛠 自动添加 22 端口规则到对应安全组"}
            </button>
          )}

          {run.isError && (
            <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded p-2.5">
              {(run.error as Error).message}
            </div>
          )}
        </div>

        <div className="px-6 py-3 border-t border-white/40 flex justify-end gap-2">
          <button className="btn" onClick={() => run.mutate()} disabled={run.isPending}>↻ 重新检查</button>
          <button className="btn-primary" onClick={onClose}>关闭</button>
        </div>
      </motion.div>
    </div>
  );
}

function InstanceTable({ items, selected, setSelected, handlers }: {
  items: Instance[]; selected: Record<string, boolean>; setSelected: (s: Record<string, boolean>) => void; handlers: any;
}) {
  const { setSSH, collect, singleAction, openSchedule, openShell } = handlers;
  return (
    <div className="glass !p-0 overflow-x-auto">
      <table className="table-clean">
        <thead>
          <tr>
            <th className="w-8 pl-4">
              <input type="checkbox"
                checked={items.length > 0 && items.every((i) => selected[keyOf(i)])}
                onChange={(e) => {
                  const v = e.target.checked;
                  const s = { ...selected };
                  items.forEach((i) => { s[keyOf(i)] = v; });
                  setSelected(s);
                }} />
            </th>
            <th>账号</th>
            <th>实例</th>
            <th>状态</th>
            <th>规格</th>
            <th>区域</th>
            <th>公网 IP</th>
            <th>本月用量</th>
            <th>SSH</th>
            <th>活跃</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {items.map((i) => (
            <tr key={keyOf(i)}>
              <td className="pl-4">
                <input type="checkbox" checked={!!selected[keyOf(i)]}
                  onChange={(e) => setSelected({ ...selected, [keyOf(i)]: e.target.checked })} />
              </td>
              <td>
                <div className="flex items-center gap-2">
                  <ProviderTag provider={i.account_provider} />
                  <span className="text-xs text-slate-600 truncate max-w-[100px]">{i.account_name}</span>
                </div>
              </td>
              <td>
                <div className="font-medium text-slate-900">{i.name || i.id}</div>
                <div className="text-[11px] text-slate-400 font-mono">{i.id}</div>
              </td>
              <td><StateBadge state={i.state} /></td>
              <td className="font-mono text-xs">{i.instance_type}<div className="text-[11px] text-slate-400">{i.disk_gb ? `${i.disk_gb}G` : ""} {i.arch}</div></td>
              <td className="font-mono text-xs">{i.region}<div className="text-[11px] text-slate-400">{i.zone}</div></td>
              <td className="font-mono text-xs">{i.public_ip || <span className="text-slate-400">—</span>}</td>
              <td className="min-w-[140px]">
                <div className="text-xs text-slate-600 tabular-nums mb-1">
                  {fmtBytes(i.monthly_traffic_gb)} / {i.traffic_limit_gb.toFixed(1)} GB
                </div>
                <ProgressBar pct={i.monthly_traffic_pct} height={4} />
              </td>
              <td>
                <input className="input !py-0.5 !px-2 w-20 text-xs" placeholder="user"
                  defaultValue={i.ssh_user}
                  onBlur={(e) => { if (e.target.value !== i.ssh_user) setSSH.mutate({ account_id: i.account_id, id: i.id, ssh_user: e.target.value, ssh_port: i.ssh_port || 22 }); }} />
              </td>
              <td className="text-xs text-slate-600">{fmtAgo(i.last_alive_at)}</td>
              <td>
                <div className="flex gap-1">
                  <button className="btn btn-sm" disabled={i.state === "running"} onClick={() => singleAction.mutate({ kind: "start", inst: i })}>启</button>
                  <button className="btn btn-sm" disabled={i.state !== "running"} onClick={() => singleAction.mutate({ kind: "stop", inst: i })}>停</button>
                  <button className="btn btn-sm" disabled={i.state !== "running" || !i.public_ip} onClick={() => openShell(i)}>💻</button>
                  <button className="btn btn-sm" onClick={() => collect.mutate(i)}>采</button>
                  <button className="btn btn-sm" onClick={() => openSchedule(i)}>⏱</button>
                  <button className="btn-danger btn-sm" onClick={() => confirm(`销毁 ${i.id}？`) && singleAction.mutate({ kind: "terminate", inst: i })}>毁</button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ====================== 实例定时任务弹窗 ====================== */
const CRON_PRESETS: { label: string; cron: string; tip: string }[] = [
  { label: "每天 23:00", cron: "0 23 * * *", tip: "每日固定时刻" },
  { label: "每天 03:00", cron: "0 3 * * *", tip: "凌晨低峰" },
  { label: "每周日 23:00", cron: "0 23 * * 0", tip: "周末" },
  { label: "每月 1 号 00:00", cron: "0 0 1 * *", tip: "月初" },
  { label: "每月 28 号 23:00", cron: "0 23 28 * *", tip: "月末" },
  { label: "30 分钟后（一次性近似）", cron: "", tip: "用 +30min" },
];

const ACTION_INFO: Record<string, { label: string; cls: string; tip: string }> = {
  start:   { label: "启动",   cls: "from-emerald-500 to-teal-500",   tip: "把实例开机" },
  stop:    { label: "停止",   cls: "from-slate-500 to-slate-600",    tip: "关闭实例（保留磁盘）" },
  restart: { label: "重启",   cls: "from-sky-500 to-cyan-500",       tip: "停止后立即启动" },
  destroy: { label: "销毁",   cls: "from-red-500 to-rose-500",       tip: "彻底删除实例（不可恢复）" },
};

function ScheduleModal({ inst, onClose }: { inst: Instance; onClose: () => void }) {
  const qc = useQueryClient();
  const [action, setAction] = useState<"start" | "stop" | "restart" | "destroy">("destroy");
  const [triggerType, setTriggerType] = useState<"date" | "cron">("date");
  const [runAt, setRunAt] = useState(defaultRunAtLocal());
  const [cron, setCron] = useState("0 23 * * *");
  const [note, setNote] = useState("");

  const list = useQuery({
    queryKey: ["schedules", inst.account_id, inst.id],
    queryFn: async () => {
      const all = (await api.get<Schedule[]>(`/accounts/${inst.account_id}/schedules`)).data;
      return all.filter((s) => s.instance_id === inst.id);
    },
  });

  const create = useMutation({
    mutationFn: async () => {
      const body: any = {
        instance_id: inst.id, action, trigger_type: triggerType,
        enabled: true, note,
        cron: triggerType === "cron" ? cron : "",
        run_at: triggerType === "date" ? new Date(runAt).toISOString() : null,
      };
      return api.post(`/accounts/${inst.account_id}/schedules`, body);
    },
    onSuccess: () => {
      setNote("");
      qc.invalidateQueries({ queryKey: ["schedules", inst.account_id, inst.id] });
      qc.invalidateQueries({ queryKey: ["schedules"] });
    },
  });

  const del = useMutation({
    mutationFn: async (id: number) => api.delete(`/accounts/${inst.account_id}/schedules/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["schedules", inst.account_id, inst.id] }),
  });

  const toggle = useMutation({
    mutationFn: async (s: Schedule) => api.put(`/accounts/${inst.account_id}/schedules/${s.id}`, { ...s, enabled: !s.enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["schedules", inst.account_id, inst.id] }),
  });

  const info = ACTION_INFO[action];
  const isDestroy = action === "destroy";
  const submitDisabled = create.isPending || (triggerType === "cron" ? !cron : !runAt);

  return (
    <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-sm flex items-center justify-center z-10 p-4">
      <motion.div
        initial={{ opacity: 0, scale: 0.95, y: 20 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        className="glass w-[640px] max-h-[90vh] overflow-y-auto !p-0"
      >
        <div className="px-6 py-4 border-b border-white/40 flex items-start justify-between">
          <div>
            <h3 className="text-lg font-bold gradient-text">实例定时任务</h3>
            <div className="text-xs text-slate-500 mt-0.5">
              <ProviderTag provider={inst.account_provider} /> <span className="ml-2 font-mono">{inst.name || inst.id}</span>
            </div>
          </div>
          <button className="btn-ghost btn-sm" onClick={onClose}>✕</button>
        </div>

        <div className="p-6 space-y-5">
          {/* 动作选择 */}
          <div>
            <label className="label">选择动作</label>
            <div className="grid grid-cols-4 gap-2">
              {(Object.keys(ACTION_INFO) as ("start" | "stop" | "restart" | "destroy")[]).map((k) => {
                const a = ACTION_INFO[k];
                const sel = action === k;
                return (
                  <button
                    key={k}
                    onClick={() => setAction(k)}
                    className={`relative px-3 py-3 rounded-xl border transition-all ${
                      sel
                        ? `bg-gradient-to-r ${a.cls} text-white border-transparent shadow-lg`
                        : "bg-white/60 border-white/50 hover:border-slate-300 hover:bg-white text-slate-700"
                    }`}
                  >
                    <div className="font-semibold text-sm">{a.label}</div>
                    <div className={`text-[11px] mt-0.5 ${sel ? "text-white/80" : "text-slate-400"}`}>{a.tip}</div>
                  </button>
                );
              })}
            </div>
            {isDestroy && (
              <div className="mt-3 px-3 py-2 rounded-lg bg-red-50/80 border border-red-200/60 text-xs text-red-700">
                ⚠ <b>不可恢复</b>。到时间会调用 terminate API 彻底删除该实例（含磁盘）。
              </div>
            )}
          </div>

          {/* 触发类型切换 */}
          <div>
            <label className="label">触发方式</label>
            <div className="flex rounded-lg border border-white/50 bg-white/60 p-0.5 text-xs mb-3 w-fit">
              <button
                className={`px-4 py-1.5 rounded-md transition-all ${
                  triggerType === "date"
                    ? "bg-gradient-to-r from-indigo-600 to-violet-600 text-white shadow-sm"
                    : "text-slate-600 hover:text-slate-900"
                }`}
                onClick={() => setTriggerType("date")}
              >
                ⏰ 一次性（指定日期时间）
              </button>
              <button
                className={`px-4 py-1.5 rounded-md transition-all ${
                  triggerType === "cron"
                    ? "bg-gradient-to-r from-indigo-600 to-violet-600 text-white shadow-sm"
                    : "text-slate-600 hover:text-slate-900"
                }`}
                onClick={() => setTriggerType("cron")}
              >
                🔁 周期（cron）
              </button>
            </div>

            {triggerType === "date" ? (
              <div>
                <input
                  type="datetime-local"
                  className="input font-mono"
                  value={runAt}
                  onChange={(e) => setRunAt(e.target.value)}
                  min={defaultRunAtLocal()}
                />
                <div className="flex flex-wrap gap-1.5 mt-2">
                  {RELATIVE_PRESETS.map((p) => (
                    <button
                      key={p.label}
                      className="btn btn-sm"
                      onClick={() => setRunAt(toLocalDtInput(p.fn()))}
                    >{p.label}</button>
                  ))}
                </div>
                <div className="text-[11px] text-slate-400 mt-2">
                  {runAt ? `将在 ${new Date(runAt).toLocaleString()} 执行一次（本地时区）` : "选择执行时间"}
                </div>
              </div>
            ) : (
              <div>
                <div className="grid grid-cols-2 gap-2 mb-2">
                  {CRON_PRESETS.map((p) => (
                    <button
                      key={p.label}
                      onClick={() => setCron(p.cron)}
                      className={`text-left px-3 py-2 rounded-lg border text-xs transition-all ${
                        cron === p.cron
                          ? "bg-indigo-50 border-indigo-300 text-indigo-900 shadow-sm"
                          : "bg-white/60 border-white/50 hover:border-slate-300 text-slate-700"
                      }`}
                    >
                      <div className="font-semibold">{p.label}</div>
                      <div className="text-[11px] text-slate-500 font-mono">{p.cron}</div>
                    </button>
                  ))}
                </div>
                <input className="input font-mono" value={cron} onChange={(e) => setCron(e.target.value)} placeholder="自定义 cron (5 段)" />
                <div className="text-[11px] text-slate-400 mt-1">格式：<span className="font-mono">分 时 日 月 周</span></div>
              </div>
            )}
          </div>

          <div>
            <label className="label">备注（可选）</label>
            <input className="input" placeholder="如：试用到期前销毁" value={note} onChange={(e) => setNote(e.target.value)} />
          </div>

          {create.isError && (
            <div className="text-sm text-red-600 bg-red-50/80 border border-red-200/60 rounded-lg p-2.5">
              {(create.error as Error).message}
            </div>
          )}

          <button
            className={`w-full py-2.5 rounded-lg text-white font-semibold shadow-lg transition-all
              bg-gradient-to-r ${info.cls}
              hover:shadow-xl hover:-translate-y-0.5 disabled:opacity-50`}
            disabled={submitDisabled}
            onClick={() => create.mutate()}
          >
            {create.isPending ? "创建中…" : `+ 创建「${info.label}」${triggerType === "date" ? "一次性" : "周期"}任务`}
          </button>

          <div>
            <div className="label">该实例的现有定时任务</div>
            {list.data?.length === 0 && (
              <div className="text-xs text-slate-400 px-3 py-3 rounded-lg bg-slate-50/60">暂无</div>
            )}
            <div className="space-y-2">
              {list.data?.map((s) => {
                const a = ACTION_INFO[s.action] || ACTION_INFO.stop;
                const expired = s.trigger_type === "date" && !s.enabled;
                return (
                  <ScheduleRow key={s.id} s={s} a={a} expired={expired}
                    onToggle={() => toggle.mutate(s)}
                    onDelete={() => confirm("删除？") && del.mutate(s.id)} />
                );
              })}
            </div>
          </div>
        </div>
      </motion.div>
    </div>
  );
}

const RELATIVE_PRESETS: { label: string; fn: () => Date }[] = [
  { label: "+1小时", fn: () => new Date(Date.now() + 3600_000) },
  { label: "+6小时", fn: () => new Date(Date.now() + 6 * 3600_000) },
  { label: "今晚23点", fn: () => { const d = new Date(); d.setHours(23, 0, 0, 0); if (d <= new Date()) d.setDate(d.getDate() + 1); return d; } },
  { label: "明天0点", fn: () => { const d = new Date(); d.setDate(d.getDate() + 1); d.setHours(0, 0, 0, 0); return d; } },
  { label: "+7天", fn: () => new Date(Date.now() + 7 * 86400_000) },
  { label: "+30天", fn: () => new Date(Date.now() + 30 * 86400_000) },
];

function ScheduleRow({ s, a, expired, onToggle, onDelete }: {
  s: Schedule; a: { label: string; cls: string }; expired: boolean;
  onToggle: () => void; onDelete: () => void;
}) {
  const [showLog, setShowLog] = useState(false);
  const log = useQuery({
    enabled: showLog,
    queryKey: ["schedule-audit", s.id],
    queryFn: async () => (await api.get<any[]>("/audit", { params: { schedule_id: s.id, limit: 50 } })).data,
  });
  return (
    <div className="rounded-lg bg-white/60 border border-white/50 overflow-hidden">
      <div className="flex items-center gap-3 px-3 py-2">
        <span className={`tag bg-gradient-to-r ${a.cls} text-white`}>{a.label}</span>
        <span className="text-[11px] text-slate-500">
          {s.trigger_type === "date" ? "⏰ 一次" : "🔁 周期"}
        </span>
        <span className="font-mono text-xs text-slate-700">
          {s.trigger_type === "date" && s.run_at
            ? new Date(s.run_at).toLocaleString()
            : s.cron}
        </span>
        {s.note && <span className="text-xs text-slate-500 truncate flex-1">{s.note}</span>}
        <span className={`text-[11px] ${expired ? "text-slate-400" : s.enabled ? "text-emerald-600" : "text-slate-400"}`}>
          {expired ? "✓ 已执行" : s.enabled ? "● 启用" : "○ 暂停"}
        </span>
        <button className="btn-ghost btn-sm" onClick={() => setShowLog((v) => !v)}>📜 记录</button>
        {!expired && (
          <button className="btn-ghost btn-sm" onClick={onToggle}>{s.enabled ? "暂停" : "启用"}</button>
        )}
        <button className="btn-danger btn-sm" onClick={onDelete}>删除</button>
      </div>
      {showLog && (
        <div className="px-3 py-2 border-t border-white/40 bg-slate-50/40 text-xs">
          {log.isLoading && <div className="text-slate-400">加载中…</div>}
          {log.data?.length === 0 && <div className="text-slate-400">无执行记录</div>}
          {log.data?.map((r: any) => (
            <div key={r.id} className="flex items-center gap-2 py-1">
              <span className="text-slate-500 tabular-nums">{new Date(r.at).toLocaleString()}</span>
              <span className="font-mono text-slate-700">{r.action}</span>
              {r.ok
                ? <span className="text-emerald-600">✓ 成功</span>
                : <span className="text-red-600">✗ {r.error || "失败"}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function toLocalDtInput(d: Date): string {
  // <input type="datetime-local"> 需要 "YYYY-MM-DDTHH:mm" 不带 tz
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function defaultRunAtLocal(): string {
  return toLocalDtInput(new Date(Date.now() + 3600_000));
}

function genPwd(): string {
  // 16 字符强密码：大小写 + 数字 + 安全符号
  const upper = "ABCDEFGHJKLMNPQRSTUVWXYZ";
  const lower = "abcdefghijkmnpqrstuvwxyz";
  const digit = "23456789";
  const sym = "!@#$%^&*-+=";
  const all = upper + lower + digit + sym;
  const arr = new Uint32Array(16);
  crypto.getRandomValues(arr);
  let pwd = "";
  pwd += upper[arr[0] % upper.length];
  pwd += lower[arr[1] % lower.length];
  pwd += digit[arr[2] % digit.length];
  pwd += sym[arr[3] % sym.length];
  for (let i = 4; i < 16; i++) pwd += all[arr[i] % all.length];
  // shuffle
  return pwd.split("").sort(() => crypto.getRandomValues(new Uint32Array(1))[0] - 0x80000000).join("");
}

/* ====================== 创建实例弹窗（保持原逻辑，只换样式） ====================== */
function CreateInstanceModal({ accounts, defaultAccountId, onClose }: {
  accounts: Account[]; defaultAccountId?: number; onClose: () => void;
}) {
  const toast = useToast();
  const enabled = accounts.filter((a) => a.enabled);
  const [accountId, setAccountId] = useState<number>(defaultAccountId ?? enabled[0]?.id ?? 0);
  const account = enabled.find((a) => a.id === accountId) ?? enabled[0];
  const provider = (account?.provider || "aws") as "aws" | "gcp" | "oracle" | "azure";

  const regions = REGIONS[provider] || [];
  const [region, setRegion] = useState(account?.default_region || regions[0]?.value || "");
  const regionPreset = regions.find((r) => r.value === region);
  const zones = regionPreset?.zones || [];
  const [zone, setZone] = useState(zones[0] || "");

  const types = INSTANCE_TYPES[provider] || [];
  const [instanceType, setInstanceType] = useState(types[0]?.value || "");

  const images = provider === "aws" ? AWS_IMAGE_ALIASES
                : provider === "gcp" ? GCP_IMAGES
                : provider === "oracle" ? ORACLE_IMAGES
                : provider === "azure" ? AZURE_IMAGES
                : [];
  const [image, setImage] = useState(images[0]?.value || "");

  // 切换账户时重置依赖字段
  useEffect(() => {
    if (!account) return;
    const newRegions = REGIONS[account.provider as "aws" | "gcp" | "oracle" | "azure"] || [];
    const newRegion = account.default_region || newRegions[0]?.value || "";
    setRegion(newRegion);
    const newZones = newRegions.find((r) => r.value === newRegion)?.zones || [];
    setZone(newZones[0] || "");
    const newTypes = INSTANCE_TYPES[account.provider as "aws" | "gcp" | "oracle" | "azure"] || [];
    setInstanceType(newTypes[0]?.value || "");
    const newImages = account.provider === "aws" ? AWS_IMAGE_ALIASES
                    : account.provider === "gcp" ? GCP_IMAGES
                    : account.provider === "oracle" ? ORACLE_IMAGES
                    : account.provider === "azure" ? AZURE_IMAGES
                    : [];
    setImage(newImages[0]?.value || "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accountId]);

  const [name, setName] = useState("ch-" + Math.random().toString(36).slice(2, 8));
  const [network, setNetwork] = useState("");
  const [firewall, setFirewall] = useState<string[]>([]);  // 多选 SG ID
  const [publicIp, setPublicIp] = useState(true);
  const [userData, setUserData] = useState("");
  const [diskSize, setDiskSize] = useState(30);
  const diskTypes = DISK_TYPES[provider] || [];
  const [diskType, setDiskType] = useState(diskTypes[0]?.value || "");

  // 该 region 下的安全组列表
  const sgs = useQuery({
    enabled: !!accountId && !!region && (provider === "aws" || provider === "gcp" || provider === "oracle"),
    queryKey: ["sgs", accountId, region],
    queryFn: async () => (await api.get<{ id: string; name: string; description: string; vpc_id: string }[]>(
      `/accounts/${accountId}/instances/options/security-groups`,
      { params: { region } }
    )).data,
  });

  const [enablePwdLogin, setEnablePwdLogin] = useState(true);
  const [enableRootLogin, setEnableRootLogin] = useState(true);
  const [rootPwd, setRootPwd] = useState(genPwd());
  const [showPwd, setShowPwd] = useState(false);

  const create = useMutation({
    mutationFn: async () => {
      if (!accountId) throw new Error("请先选择账户");
      if (enablePwdLogin && !rootPwd) throw new Error("勾选了密码登录，必须填密码");
      await api.post(`/accounts/${accountId}/instances`, {
        name, region, zone, instance_type: instanceType, image,
        network, firewall_groups: firewall,
        public_ip: publicIp, tags: {}, user_data: userData,
        disk_size_gb: diskSize, disk_type: diskType,
        enable_password_login: enablePwdLogin,
        enable_root_login: enableRootLogin,
        root_password: enablePwdLogin ? rootPwd : "",
      });
    },
    onSuccess: () => {
      toast.show("实例创建成功，cloud-init 配置密码可能需要 1-2 分钟", "success");
      onClose();
    },
    onError: (e: Error) => toast.show(e.message, "error"),
  });

  // 切磁盘类型预设也跟着 provider 走
  useEffect(() => {
    const newDiskTypes = DISK_TYPES[provider] || [];
    setDiskType(newDiskTypes[0]?.value || "");
  }, [provider]);

  const isFreeRegion = regionPreset?.free_tier;
  const isFreeType = types.find((t) => t.value === instanceType)?.free_tier;

  if (enabled.length === 0) {
    return (
      <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-sm flex items-center justify-center z-10 p-4">
        <div className="glass w-[440px] !p-6 text-center">
          <div className="text-base font-semibold mb-2">尚未配置云账户</div>
          <div className="text-sm text-slate-500 mb-4">请先到 "云账户" 页添加 AWS / GCP 账户</div>
          <button className="btn-primary" onClick={onClose}>知道了</button>
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-sm flex items-center justify-center z-10 p-4">
      <motion.div
        initial={{ opacity: 0, scale: 0.95, y: 20 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        className="glass w-[640px] max-h-[90vh] overflow-y-auto !p-0"
      >
        <div className="px-6 py-4 border-b border-white/40">
          <h3 className="text-lg font-bold gradient-text">创建实例</h3>
          <p className="text-xs text-slate-500 mt-0.5">★ 标记的为永久免费配置</p>
        </div>
        <div className="p-6 space-y-4">
          <div>
            <label className="label">目标账户（决定云服务商）</label>
            <select className="input" value={accountId} onChange={(e) => setAccountId(Number(e.target.value))}>
              {enabled.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.provider.toUpperCase()} · {a.name}{a.group_tag ? ` [${a.group_tag}]` : ""}
                </option>
              ))}
            </select>
            <div className="text-[11px] text-slate-500 mt-1">
              切换账户会自动调整下方区域/规格/镜像选项
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div><label className="label">实例名称</label>
              <input className="input" value={name} onChange={(e) => setName(e.target.value)} /></div>
            <div><label className="label">分配公网 IP</label>
              <select className="input" value={publicIp ? "1" : "0"} onChange={(e) => setPublicIp(e.target.value === "1")}>
                <option value="1">是（采流量必须）</option>
                <option value="0">否</option>
              </select></div>
          </div>
          <div>
            <label className="label">区域 {isFreeRegion && <span className="text-emerald-600 ml-1">★ Always Free</span>}</label>
            <select className="input" value={region} onChange={(e) => { setRegion(e.target.value); setZone(""); }}>
              {regions.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
            </select>
          </div>
          {provider === "gcp" && (
            <div>
              <label className="label">可用区</label>
              <select className="input" value={zone} onChange={(e) => setZone(e.target.value)}>
                <option value="">— 选择 —</option>
                {zones.map((z) => <option key={z} value={z}>{z}</option>)}
              </select>
            </div>
          )}
          <div>
            <label className="label">规格 {isFreeType && <span className="text-emerald-600 ml-1">★ 免费</span>}</label>
            <select className="input" value={instanceType} onChange={(e) => setInstanceType(e.target.value)}>
              {types.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
            </select>
            {provider === "gcp" && instanceType === "e2-micro" && !isFreeRegion && (
              <div className="text-xs text-amber-600 mt-1">注意：e2-micro 仅在 us-west1/us-central1/us-east1 永久免费</div>
            )}
          </div>
          <div>
            <label className="label">镜像</label>
            {images.length > 0 ? (
              <select className="input" value={image} onChange={(e) => setImage(e.target.value)}>
                {images.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
              </select>
            ) : (
              <input className="input font-mono text-xs" placeholder={provider === "aws" ? "ami-xxxxxxxx" : provider === "gcp" ? "projects/.../images/family/..." : provider === "oracle" ? "oracle-8 或 ocid1.image..." : provider === "azure" ? "ubuntu-22.04 或 publisher:offer:sku" : "镜像 ID"} value={image} onChange={(e) => setImage(e.target.value)} />
            )}
          </div>

          {/* 磁盘 */}
          <div className="rounded-lg border border-slate-200/60 bg-white/40 p-3 space-y-3">
            <div className="text-[11px] font-semibold text-slate-700 uppercase tracking-wider">💾 系统盘</div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="label">磁盘大小 (GB)</label>
                <input type="number" className="input" min={8} max={2000} value={diskSize}
                  onChange={(e) => setDiskSize(Math.max(8, Number(e.target.value) || 30))} />
                <div className="flex flex-wrap gap-1 mt-1.5">
                  {DISK_SIZE_PRESETS.map((sz) => (
                    <button key={sz} type="button"
                      className={`px-2 py-0.5 text-[11px] rounded transition ${diskSize === sz ? "bg-indigo-600 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"}`}
                      onClick={() => setDiskSize(sz)}>{sz}G</button>
                  ))}
                </div>
              </div>
              <div>
                <label className="label">磁盘类型</label>
                <select className="input" value={diskType} onChange={(e) => setDiskType(e.target.value)}>
                  {diskTypes.map((d) => <option key={d.value} value={d.value}>{d.label}</option>)}
                </select>
                {diskType && diskTypes.find((d) => d.value === diskType)?.hint && (
                  <div className="text-[11px] text-slate-500 mt-1">{diskTypes.find((d) => d.value === diskType)?.hint}</div>
                )}
              </div>
            </div>
            {provider === "aws" && diskSize > 30 && (
              <div className="text-[11px] text-amber-600">⚠ AWS Free Tier 仅前 30GB 免费，超出按月计费</div>
            )}
          </div>

          {/* 安全组 / Firewall 标签 */}
          <div>
            <label className="label">
              {provider === "aws" ? "安全组" : "Firewall 标签"}
              <span className="font-normal text-slate-400 ml-2">（可选，留空使用默认）</span>
            </label>
            {sgs.isLoading ? (
              <div className="text-xs text-slate-400 px-2 py-3">加载中…</div>
            ) : (sgs.data && sgs.data.length > 0) ? (
              <div className="rounded-lg border border-slate-200/60 bg-white/60 max-h-48 overflow-y-auto">
                {sgs.data.map((sg) => (
                  <label key={sg.id} className="flex items-start gap-2 px-3 py-2 hover:bg-slate-50 cursor-pointer border-b border-slate-100 last:border-0">
                    <input type="checkbox" className="mt-0.5"
                      checked={firewall.includes(sg.id)}
                      onChange={(e) => {
                        if (e.target.checked) setFirewall([...firewall, sg.id]);
                        else setFirewall(firewall.filter((x) => x !== sg.id));
                      }} />
                    <div className="flex-1 min-w-0">
                      <div className="font-medium text-sm text-slate-800">{sg.name} <span className="text-[11px] text-slate-400 font-mono">{sg.id}</span></div>
                      {sg.description && <div className="text-[11px] text-slate-500 truncate">{sg.description}</div>}
                      {sg.vpc_id && <div className="text-[11px] text-slate-400 font-mono">VPC: {sg.vpc_id}</div>}
                    </div>
                  </label>
                ))}
              </div>
            ) : sgs.isError ? (
              <div className="text-[11px] text-red-600 bg-red-50/50 border border-red-200/50 rounded px-2 py-1.5">
                拉取失败：{(sgs.error as Error).message}
              </div>
            ) : (
              <div className="text-[11px] text-slate-500 bg-slate-50/50 rounded px-2 py-1.5">
                未找到{provider === "aws" ? "安全组" : "firewall 标签"}，将使用 default
              </div>
            )}
            {firewall.length > 0 && (
              <div className="text-[11px] text-slate-500 mt-1">已选 {firewall.length} 个</div>
            )}
          </div>

          <div className="rounded-lg border border-indigo-200/50 bg-indigo-50/30 p-3 space-y-3">
            <div className="text-[11px] font-semibold text-indigo-700 uppercase tracking-wider">🔐 SSH 登录</div>
            <label className="text-sm flex items-center gap-2">
              <input type="checkbox" checked={enablePwdLogin} onChange={(e) => setEnablePwdLogin(e.target.checked)} />
              <span>启用密码登录（cloud-init 自动配置 sshd_config）</span>
            </label>
            <label className="text-sm flex items-center gap-2">
              <input type="checkbox" checked={enableRootLogin} onChange={(e) => setEnableRootLogin(e.target.checked)} disabled={!enablePwdLogin} />
              <span className={enablePwdLogin ? "" : "text-slate-400"}>允许 root 直接登录</span>
            </label>
            {enablePwdLogin && (
              <div>
                <label className="label">root 密码</label>
                <div className="flex gap-2">
                  <input type={showPwd ? "text" : "password"} className="input flex-1 font-mono"
                    value={rootPwd} onChange={(e) => setRootPwd(e.target.value)} />
                  <button type="button" className="btn" onClick={() => setShowPwd((v) => !v)}>{showPwd ? "🙈" : "👁"}</button>
                  <button type="button" className="btn" onClick={() => setRootPwd(genPwd())}>🎲</button>
                  <button type="button" className="btn" onClick={() => { navigator.clipboard.writeText(rootPwd); toast.show("已复制到剪贴板", "success"); }}>📋</button>
                </div>
                <div className="text-[11px] text-slate-500 mt-1">
                  实例创建后 CloudHelper 自动用此密码登录。<b>请确保已复制保存！</b>
                </div>
              </div>
            )}
          </div>

          <details className="text-sm">
            <summary className="cursor-pointer text-slate-500 hover:text-slate-700 font-medium">高级选项</summary>
            <div className="mt-3 space-y-3 pl-3 border-l-2 border-indigo-200">
              <div><label className="label">{provider === "aws" ? "子网（留空用默认 VPC）" : "网络"}</label>
                <input className="input font-mono text-xs" placeholder={provider === "aws" ? "subnet-xxx" : "global/networks/default"} value={network} onChange={(e) => setNetwork(e.target.value)} /></div>
              <div><label className="label">User Data / Startup（追加到密码 cloud-init 之后）</label>
                <textarea className="input h-20 font-mono text-xs" value={userData} onChange={(e) => setUserData(e.target.value)} /></div>
            </div>
          </details>

          {create.isError && (
            <div className="text-sm text-red-600 bg-red-50/80 border border-red-200/60 rounded-lg p-2.5">{(create.error as Error).message}</div>
          )}
        </div>
        <div className="px-6 py-3 border-t border-white/40 flex justify-end gap-2">
          <button className="btn" onClick={onClose}>取消</button>
          <button className="btn-primary" disabled={create.isPending} onClick={() => create.mutate()}>
            {create.isPending ? <><Spinner /> 创建中…</> : "创建"}
          </button>
        </div>
      </motion.div>
    </div>
  );
}
