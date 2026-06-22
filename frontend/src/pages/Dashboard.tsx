import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { api, Account, BudgetSummary, DashboardSummary, Instance } from "../lib/api";
import { AnimatedNumber, PageHeader, ProgressBar, ProviderTag, StateBadge, fmtAgo, fmtBytes } from "../lib/components";

export default function Dashboard() {
  const summary = useQuery({
    queryKey: ["dashboard"],
    queryFn: async () => (await api.get<DashboardSummary>("/fleet/dashboard")).data,
    refetchInterval: 30_000,
  });

  const instances = useQuery({
    queryKey: ["fleet-instances"],
    queryFn: async () => (await api.get<Instance[]>("/fleet/instances")).data,
    refetchInterval: 30_000,
  });

  const accounts = useQuery({
    queryKey: ["accounts"],
    queryFn: async () => (await api.get<Account[]>("/accounts")).data,
  });

  const s = summary.data;
  const list = (instances.data || []).slice().sort((a, b) => b.monthly_traffic_pct - a.monthly_traffic_pct);
  // 显示赠金卡片：有信用额度 OR AWS（要看 Free Tier）
  const accs = (accounts.data || []).filter((a) => a.credit_total_usd > 0 || a.provider === "aws" || a.provider === "gcp" || true);
  // 显示所有账户，因为现在都有实时账单

  return (
    <div>
      <PageHeader
        title="总览"
        description={s ? `共 ${s.accounts_total} 个云账号 · ${s.instances_total} 台实例` : "加载中…"}
      />

      {s && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          <Stat label="账户总数" value={s.accounts_total} accent="indigo"
                sub={Object.entries(s.accounts_by_provider).map(([k, v]) => `${k.toUpperCase()} ${v}`).join(" · ") || "—"} />
          <Stat label="运行中" value={s.instances_running} accent="emerald"
                sub={`${s.instances_stopped} 台停止`} />
          <Stat label="本月流量" value={s.monthly_traffic_gb_total} decimals={2} suffix=" GB" accent="violet"
                sub={s.last_collected_at ? `采集 ${fmtAgo(s.last_collected_at)}` : "未采集"} />
          <Stat label="超限实例" value={s.over_limit_count} accent={s.over_limit_count > 0 ? "rose" : "slate"}
                sub={s.over_limit_count > 0 ? "已自动关机" : "全部正常"} />
        </div>
      )}

      {accs.length > 0 && (
        <div className="mb-6">
          <h3 className="font-semibold text-slate-900 mb-3 flex items-center gap-2">
            💰 赠金状态
            <span className="text-xs text-slate-400 font-normal">基于实例规格估算，仅供参考</span>
          </h3>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {accs.map((a) => <BudgetCard key={a.id} account={a} />)}
          </div>
        </div>
      )}

      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.1 }}
        className="glass p-6"
      >
        <div className="flex items-center justify-between mb-5">
          <div>
            <h3 className="font-semibold text-slate-900">实例流量明细</h3>
            <div className="text-xs text-slate-500 mt-0.5">按使用率倒序，每 30 秒自动刷新</div>
          </div>
          <span className="tag tag-info">实时</span>
        </div>
        {list.length === 0 ? (
          <div className="text-slate-400 text-sm py-12 text-center">
            暂无实例。先到 <span className="font-mono text-slate-600">"云账户"</span> 加账号，再到 <span className="font-mono text-slate-600">"实例"</span> 页点 ↻ 从云刷新。
          </div>
        ) : (
          <table className="table-clean">
            <thead>
              <tr>
                <th>账号</th>
                <th>实例</th>
                <th>状态</th>
                <th className="w-1/3">本月用量</th>
                <th>日预算</th>
                <th>最近活跃</th>
              </tr>
            </thead>
            <tbody>
              {list.map((i, idx) => (
                <motion.tr
                  key={`${i.account_id}:${i.id}`}
                  initial={{ opacity: 0, x: -10 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: idx * 0.03 }}
                >
                  <td>
                    <div className="flex items-center gap-2">
                      <ProviderTag provider={i.account_provider} />
                      <span className="text-xs text-slate-600 truncate max-w-[120px]">{i.account_name}</span>
                    </div>
                  </td>
                  <td>
                    <div className="font-medium text-slate-900">{i.name || i.id}</div>
                    <div className="text-[11px] text-slate-400 font-mono">{i.public_ip || "no public ip"}</div>
                  </td>
                  <td><StateBadge state={i.state} /></td>
                  <td>
                    <div className="flex items-center gap-3">
                      <div className="flex-1"><ProgressBar pct={i.monthly_traffic_pct} /></div>
                      <div className="text-xs text-slate-500 tabular-nums whitespace-nowrap">
                        {fmtBytes(i.monthly_traffic_out_gb)} / {i.traffic_limit_gb.toFixed(1)} GB
                      </div>
                    </div>
                  </td>
                  <td className="text-xs tabular-nums">
                    {i.daily_usd > 0
                      ? <span className="text-slate-700">${i.daily_usd.toFixed(3)}/天</span>
                      : <span className="text-emerald-600">免费</span>}
                  </td>
                  <td>
                    <div className="text-xs">{fmtAgo(i.last_alive_at)}</div>
                    {i.last_collect_error && (
                      <div className="text-[11px] text-red-500 max-w-[200px] truncate" title={i.last_collect_error}>
                        ⚠ {i.last_collect_error}
                      </div>
                    )}
                  </td>
                </motion.tr>
              ))}
            </tbody>
          </table>
        )}
      </motion.div>
    </div>
  );
}

const ACCENT_GRAD: Record<string, string> = {
  indigo: "from-indigo-500 via-violet-500 to-purple-500",
  emerald: "from-emerald-500 to-teal-500",
  violet: "from-violet-500 via-fuchsia-500 to-pink-500",
  rose: "from-red-500 to-rose-500",
  slate: "from-slate-400 to-slate-500",
};

function Stat({ label, value, sub, accent = "slate", decimals = 0, suffix = "" }: {
  label: string; value: number; sub?: string; accent?: string; decimals?: number; suffix?: string;
}) {
  const grad = ACCENT_GRAD[accent] || ACCENT_GRAD.slate;
  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
      whileHover={{ y: -3 }}
      className="glass glass-hover p-5 relative overflow-hidden"
    >
      <div className={`absolute -top-8 -right-8 w-24 h-24 rounded-full bg-gradient-to-br ${grad} opacity-10 blur-2xl`} />
      <div className="text-[11px] font-semibold text-slate-500 uppercase tracking-widest">{label}</div>
      <div className={`text-4xl font-black mt-2 tabular-nums bg-gradient-to-r ${grad} bg-clip-text text-transparent`}>
        <AnimatedNumber value={value} decimals={decimals} suffix={suffix} />
      </div>
      {sub && <div className="text-xs text-slate-400 mt-1.5">{sub}</div>}
    </motion.div>
  );
}

function BudgetCard({ account }: { account: Account }) {
  const q = useQuery({
    queryKey: ["budget", account.id],
    queryFn: async () => (await api.get<BudgetSummary>(`/accounts/${account.id}/budget`)).data,
    refetchInterval: 60_000,
  });
  const ft = useQuery({
    enabled: account.provider === "aws" || account.provider === "gcp",
    queryKey: ["free-tier", account.id],
    queryFn: async () => (await api.get<{ supported: boolean; items: any[] }>(`/accounts/${account.id}/budget/free-tier`)).data,
    refetchInterval: 300_000,
    retry: 0,
  });
  const realtime = useQuery({
    queryKey: ["realtime-billing", account.id],
    queryFn: async () => (await api.get<{
      month_to_date_usd: number; tick_count: number; last_tick_at: string | null;
      last_tick_cost: number; last_tick_running: number; hourly_avg_usd: number;
    }>(`/accounts/${account.id}/budget/realtime`)).data,
    refetchInterval: 30_000,
  });
  const b = q.data;
  if (!b) {
    return <div className="glass p-5"><div className="text-sm text-slate-400">加载…</div></div>;
  }
  const usedPct = b.credit_total_usd > 0 ? (b.credit_used_usd / b.credit_total_usd) * 100 : 0;
  const willOutlast = b.will_outlast_expiry;
  const expireSoon = b.days_to_expiry !== null && b.days_to_expiry <= 14;
  const runOutSoon = b.days_until_credit_runs_out !== null && b.days_until_credit_runs_out <= 14;
  const danger = (willOutlast === false) || expireSoon || runOutSoon;

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      whileHover={{ y: -3 }}
      className={`glass glass-hover p-5 relative overflow-hidden ${danger ? "ring-1 ring-red-300/60" : ""}`}
    >
      <div className="flex items-center gap-2 mb-3">
        <ProviderTag provider={b.provider} />
        <span className="font-semibold text-slate-900 truncate">{b.account_name}</span>
      </div>

      {b.credit_total_usd > 0 && (
        <>
          <div className="flex items-end justify-between mb-1">
            <div>
              <div className="text-[11px] text-slate-500 uppercase tracking-wider">余额</div>
              <div className="text-2xl font-bold tabular-nums">
                <span className="text-slate-900">${b.credit_remaining_usd.toFixed(2)}</span>
                <span className="text-sm text-slate-400 ml-1">/ ${b.credit_total_usd.toFixed(0)}</span>
              </div>
            </div>
            <div className={`text-xs font-semibold tabular-nums ${usedPct >= 80 ? "text-red-600" : usedPct >= 50 ? "text-amber-600" : "text-emerald-600"}`}>
              已用 {usedPct.toFixed(0)}%
            </div>
          </div>
          <ProgressBar pct={usedPct} />

          <div className="grid grid-cols-2 gap-3 mt-4 text-xs">
            <div>
              <div className="text-slate-400">日预算</div>
              <div className="font-semibold text-slate-800 tabular-nums">${b.daily_burn_usd.toFixed(3)}/天</div>
            </div>
            <div>
              <div className="text-slate-400">月预估</div>
              <div className="font-semibold text-slate-800 tabular-nums">${b.monthly_burn_usd.toFixed(2)}/月</div>
            </div>
            <div>
              <div className="text-slate-400">到期</div>
              <div className={`font-semibold tabular-nums ${expireSoon ? "text-red-600" : "text-slate-800"}`}>
                {b.credit_expires_at
                  ? `${b.credit_expires_at}${b.days_to_expiry !== null ? ` (${b.days_to_expiry}天)` : ""}`
                  : "未设置"}
              </div>
            </div>
            <div>
              <div className="text-slate-400">余额可撑</div>
              <div className={`font-semibold tabular-nums ${runOutSoon ? "text-red-600" : "text-slate-800"}`}>
                {b.daily_burn_usd > 0 && b.days_until_credit_runs_out !== null
                  ? `${b.days_until_credit_runs_out} 天`
                  : "—"}
              </div>
            </div>
          </div>
        </>
      )}

      {/* 实时账单推算（30 分钟 tick 累计） */}
      {realtime.data && realtime.data.tick_count > 0 && (
        <div className="mt-4 pt-4 border-t border-white/40">
          <div className="flex items-center justify-between mb-2">
            <div className="text-[11px] font-semibold text-slate-600 uppercase tracking-wider">
              ⚡ 实时账单推算
            </div>
            <span className="text-[10px] text-slate-400">{realtime.data.tick_count} ticks</span>
          </div>
          <div className="grid grid-cols-2 gap-3 text-xs">
            <div>
              <div className="text-slate-400">本月已花 (推算)</div>
              <div className="font-bold text-slate-800 tabular-nums">
                ${realtime.data.month_to_date_usd.toFixed(4)}
              </div>
            </div>
            <div>
              <div className="text-slate-400">最近 24h 时均</div>
              <div className="font-bold text-slate-800 tabular-nums">
                ${realtime.data.hourly_avg_usd.toFixed(4)}/h
              </div>
            </div>
          </div>
          {realtime.data.last_tick_at && (
            <div className="text-[10px] text-slate-400 mt-2">
              最近 tick：{fmtAgo(realtime.data.last_tick_at)} · {realtime.data.last_tick_running} 实例运行中 · ${realtime.data.last_tick_cost.toFixed(4)}
            </div>
          )}
          <div className="text-[10px] text-slate-400 mt-1">
            💡 仅含实例运行时长，不含流量/存储/附加服务
          </div>
        </div>
      )}

      {/* Free Tier / 已花费 真实用量 */}
      {ft.data?.supported && ft.data.items.length > 0 && (
        <div className="mt-4 pt-4 border-t border-white/40">
          <div className="text-[11px] font-semibold text-slate-600 uppercase tracking-wider mb-2">
            {account.provider === "aws" ? "🎯 AWS Free Tier 用量" : "💸 本月已花费 (BigQuery 账单)"}
          </div>
          <div className="space-y-2">
            {ft.data.items.slice(0, 8).map((i, idx) => {
              // GCP 用 USD 显示，AWS 用百分比
              const isUSD = i.unit === "USD";
              return (
                <div key={idx} className="text-xs">
                  <div className="flex items-center justify-between mb-0.5">
                    <span className="text-slate-700 truncate flex-1 mr-2" title={i.description}>
                      {(i.description || i.service).slice(0, 50)}
                    </span>
                    <span className={`tabular-nums font-medium ${
                      isUSD
                        ? (i.actual_usage > 1 ? "text-amber-600" : "text-slate-700")
                        : (i.actual_pct >= 80 ? "text-red-600" : i.actual_pct >= 50 ? "text-amber-600" : "text-emerald-600")
                    }`}>
                      {isUSD ? `$${i.actual_usage.toFixed(4)}` : `${i.actual_pct.toFixed(0)}%`}
                    </span>
                  </div>
                  {!isUSD && <ProgressBar pct={i.actual_pct} height={3} />}
                  <div className="text-[10px] text-slate-400 mt-0.5">
                    {isUSD
                      ? `月底预计 $${i.forecasted_usage.toFixed(2)}`
                      : <>
                          {i.actual_usage.toFixed(2)} / {i.limit} {i.unit}
                          {i.forecasted_pct > i.actual_pct && (
                            <span className="ml-2">月底预计 {i.forecasted_pct.toFixed(0)}%</span>
                          )}
                        </>
                    }
                  </div>
                </div>
              );
            })}
            {ft.data.items.length > 8 && (
              <div className="text-[10px] text-slate-400">还有 {ft.data.items.length - 8} 项…</div>
            )}
          </div>
        </div>
      )}

      {ft.isError && (
        <div className="mt-3 text-[11px] text-amber-600 bg-amber-50/70 border border-amber-200/50 rounded-lg px-2.5 py-1.5">
          {(ft.error as Error)?.message || "Free Tier API 不可用（可能 IAM 缺权限）"}
        </div>
      )}

      {willOutlast === true && b.daily_burn_usd > 0 && (
        <div className="mt-3 text-[11px] text-emerald-700 bg-emerald-50/70 border border-emerald-200/50 rounded-lg px-2.5 py-1.5">
          ✓ 按当前消耗速度，余额能撑到到期日
        </div>
      )}
      {willOutlast === false && (
        <div className="mt-3 text-[11px] text-red-700 bg-red-50/70 border border-red-200/50 rounded-lg px-2.5 py-1.5">
          ⚠ 余额会在到期前 {Math.ceil((b.days_to_expiry ?? 0) - (b.days_until_credit_runs_out ?? 0))} 天用完
        </div>
      )}
      {b.daily_burn_usd === 0 && b.credit_total_usd > 0 && (
        <div className="mt-3 text-[11px] text-slate-500 bg-slate-50/70 border border-slate-200/50 rounded-lg px-2.5 py-1.5">
          当前无运行实例 · 0 消耗
        </div>
      )}
    </motion.div>
  );
}
