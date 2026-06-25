import { useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, Account, Provider } from "../lib/api";
import { PageHeader, ProviderTag } from "../lib/components";
import { useToast } from "../lib/toast";
import { DEFAULT_TRAFFIC_GB, REGIONS } from "../lib/presets";

export default function AccountsPage() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["accounts"],
    queryFn: async () => (await api.get<Account[]>("/accounts")).data,
  });

  const [filter, setFilter] = useState("");
  const [providerFilter, setProviderFilter] = useState<"" | Provider>("");
  const filtered = useMemo(() => {
    if (!data) return [];
    const s = filter.toLowerCase();
    return data.filter((a) =>
      (!s || a.name.toLowerCase().includes(s) || a.note.toLowerCase().includes(s)) &&
      (!providerFilter || a.provider === providerFilter)
    );
  }, [data, filter, providerFilter]);

  const [editing, setEditing] = useState<Account | null>(null);
  const [openCreate, setOpenCreate] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const toast = useToast();

  const del = useMutation({
    mutationFn: async (id: number) => api.delete(`/accounts/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["accounts"] }),
  });
  const test = useMutation({
    mutationFn: async (id: number) => (await api.post<{ ok: boolean; regions_total: number }>(`/accounts/${id}/test`)).data,
    onSuccess: (r) => alert(`连通成功，${r.regions_total} 个 region`),
    onError: (e: Error) => alert(`失败：${e.message}`),
  });

  const exportAll = useMutation({
    mutationFn: async () => {
      // 导出含明文凭据，后端要求重新鉴权：让用户再次输入密码（或 token）。
      const info = (await api.get<{ auth_required: boolean; username_auth: boolean }>("/auth/info")).data;
      let creds: Record<string, string> = {};
      if (info.auth_required) {
        if (info.username_auth) {
          const username = window.prompt("导出含明文凭据，请输入用户名以确认：") || "";
          const password = window.prompt("请输入密码：") || "";
          if (!username || !password) throw new Error("已取消导出");
          creds = { username, password };
        } else {
          const token = window.prompt("导出含明文凭据，请输入访问 token 以确认：") || "";
          if (!token) throw new Error("已取消导出");
          creds = { token };
        }
      }
      return (await api.post<any>("/accounts/export", creds)).data;
    },
    onSuccess: (data) => {
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `cloudhelper-accounts-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
      toast.show(`已导出 ${data.accounts?.length || 0} 个账号（含明文凭据，请妥善保管）`, "success");
    },
    onError: (e: Error) => toast.show(`导出失败：${e.message}`, "error"),
  });

  const importMut = useMutation({
    mutationFn: async (v: { body: any; overwrite: boolean }) =>
      (await api.post<{ created: number; updated: number; skipped: number; errors: any[]; total: number }>(
        "/accounts/import", v.body, { params: { overwrite: v.overwrite } }
      )).data,
    onSuccess: (r) => {
      const errs = r.errors.length ? `，${r.errors.length} 条出错` : "";
      toast.show(`导入完成：新建 ${r.created} · 覆盖 ${r.updated} · 跳过 ${r.skipped}${errs}`, "success");
      qc.invalidateQueries({ queryKey: ["accounts"] });
      if (r.errors.length) {
        console.warn("[import errors]", r.errors);
      }
    },
    onError: (e: Error) => toast.show(`导入失败：${e.message}`, "error"),
  });

  function onPickFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";  // 允许重选同一文件
    if (!file) return;
    file.text().then((text) => {
      try {
        const body = JSON.parse(text);
        if (!body.accounts || !Array.isArray(body.accounts)) {
          throw new Error("文件格式错误：缺少 accounts 数组");
        }
        const overwrite = confirm(
          `准备导入 ${body.accounts.length} 个账号。\n\n点"确定"覆盖同名账号；点"取消"则同名跳过。`
        );
        importMut.mutate({ body, overwrite });
      } catch (err: any) {
        toast.show(`文件解析失败：${err.message}`, "error");
      }
    });
  }

  return (
    <div>
      <PageHeader
        title="云账户"
        description={`共 ${data?.length || 0} 个账号`}
        actions={
          <>
            <select className="input w-28" value={providerFilter} onChange={(e) => setProviderFilter(e.target.value as Provider | "")}>
              <option value="">全部</option>
              <option value="aws">AWS</option>
              <option value="gcp">GCP</option>
              <option value="oracle">Oracle</option>
              <option value="azure">Azure</option>
            </select>
            <input className="input w-56" placeholder="搜索 名称 / 备注" value={filter} onChange={(e) => setFilter(e.target.value)} />
            <input ref={fileInputRef} type="file" accept=".json,application/json" hidden onChange={onPickFile} />
            <button className="btn" onClick={() => fileInputRef.current?.click()}
              disabled={importMut.isPending}>
              {importMut.isPending ? "导入中…" : "📥 导入"}
            </button>
            <button className="btn" onClick={() => exportAll.mutate()}
              disabled={exportAll.isPending || (data?.length || 0) === 0}>
              {exportAll.isPending ? "导出中…" : "📤 导出"}
            </button>
            <button className="btn-primary" onClick={() => setOpenCreate(true)}>+ 添加账号</button>
          </>
        }
      />

      <div className="glass !p-0 overflow-x-auto">
        <table className="table-clean">
          <thead>
            <tr>
              <th className="pl-4">类型</th>
              <th>名称</th>
              <th>默认区域</th>
              <th>分组</th>
              <th>免费流量</th>
              <th>备注</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {isLoading && <tr><td className="py-8 text-center text-slate-400" colSpan={7}>加载中…</td></tr>}
            {filtered.map((a) => (
              <tr key={a.id}>
                <td className="pl-4"><ProviderTag provider={a.provider} /></td>
                <td className="font-medium text-slate-900">{a.name}</td>
                <td className="font-mono text-xs">{a.default_region || "—"}</td>
                <td>{a.group_tag ? <span className="tag tag-info">{a.group_tag}</span> : <span className="text-slate-400">—</span>}</td>
                <td className="tabular-nums">{a.monthly_traffic_gb} GB</td>
                <td className="text-xs text-slate-500 max-w-[200px] truncate" title={a.note}>{a.note || "—"}</td>
                <td>
                  <div className="flex gap-1">
                    <button className="btn btn-sm" onClick={() => test.mutate(a.id)}>测试</button>
                    <button className="btn btn-sm" onClick={() => setEditing(a)}>编辑</button>
                    <button className="btn-danger btn-sm" onClick={() => confirm(`删除 ${a.name}？`) && del.mutate(a.id)}>删除</button>
                  </div>
                </td>
              </tr>
            ))}
            {!isLoading && filtered.length === 0 && <tr><td className="py-8 text-center text-slate-400" colSpan={7}>无匹配账户</td></tr>}
          </tbody>
        </table>
      </div>

      {openCreate && (
        <AccountModal onClose={() => { setOpenCreate(false); qc.invalidateQueries({ queryKey: ["accounts"] }); }} />
      )}
      {editing && (
        <AccountModal account={editing} onClose={() => { setEditing(null); qc.invalidateQueries({ queryKey: ["accounts"] }); }} />
      )}
    </div>
  );
}

function RegionField({ provider, value, onChange }: { provider: Provider; value: string; onChange: (v: string) => void }) {
  const presets = REGIONS[provider] || [];
  const isCustom = value !== "" && !presets.some((p) => p.value === value);
  const [custom, setCustom] = useState(isCustom);
  return (
    <div>
      <label className="label">默认区域</label>
      {custom ? (
        <div className="flex gap-2">
          <input className="input flex-1 font-mono" value={value} onChange={(e) => onChange(e.target.value)} placeholder="自定义 region" />
          <button type="button" className="btn" onClick={() => setCustom(false)}>选预设</button>
        </div>
      ) : (
        <div className="flex gap-2">
          <select className="input flex-1" value={value} onChange={(e) => onChange(e.target.value)}>
            <option value="">— 选择 —</option>
            {presets.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
          </select>
          <button type="button" className="btn" onClick={() => setCustom(true)}>自定义</button>
        </div>
      )}
    </div>
  );
}

function AccountModal({ account, onClose }: { account?: Account; onClose: () => void }) {
  const isEdit = !!account;
  const [f, setF] = useState({
    name: account?.name || "",
    provider: (account?.provider || "aws") as Provider,
    default_region: account?.default_region || "",
    group_tag: account?.group_tag || "",
    note: account?.note || "",
    monthly_traffic_gb: account?.monthly_traffic_gb ?? DEFAULT_TRAFFIC_GB.aws,
    credit_total_usd: account?.credit_total_usd ?? 0,
    credit_used_usd: account?.credit_used_usd ?? 0,
    credit_expires_at: account?.credit_expires_at || "",
    aws_access_key_id: "",
    aws_secret_access_key: "",
    gcp_sa_json: "",
    oracle_tenancy: "",
    oracle_user: "",
    oracle_fingerprint: "",
    oracle_region: "",
    oracle_compartment_id: "",
    oracle_key_pem: "",
    azure_tenant_id: "",
    azure_client_id: "",
    azure_client_secret: "",
    azure_subscription_id: "",
    azure_resource_group: "CloudHelper",
  });

  // 切换 provider 时如果用户没改过默认流量，跟着更新
  const onProviderChange = (p: Provider) => {
    const currentDefault = DEFAULT_TRAFFIC_GB[f.provider];
    const userTouched = f.monthly_traffic_gb !== currentDefault;
    setF({
      ...f,
      provider: p,
      default_region: "",
      monthly_traffic_gb: userTouched ? f.monthly_traffic_gb : DEFAULT_TRAFFIC_GB[p],
    });
  };
  const [keepCreds, setKeepCreds] = useState(isEdit);

  const save = useMutation({
    mutationFn: async () => {
      let credentials: Record<string, unknown> = {};
      if (!keepCreds || !isEdit) {
        if (f.provider === "aws") {
          if (!f.aws_access_key_id || !f.aws_secret_access_key) throw new Error("AWS 凭据不能为空");
          credentials = { access_key_id: f.aws_access_key_id, secret_access_key: f.aws_secret_access_key };
        } else if (f.provider === "gcp") {
          if (!f.gcp_sa_json.trim()) throw new Error("Service Account JSON 不能为空");
          credentials = JSON.parse(f.gcp_sa_json);
        } else if (f.provider === "oracle") {
          if (!f.oracle_tenancy || !f.oracle_user || !f.oracle_fingerprint || !f.oracle_key_pem) {
            throw new Error("Oracle 凭据必须填 tenancy / user / fingerprint / private key");
          }
          credentials = {
            tenancy: f.oracle_tenancy.trim(),
            user: f.oracle_user.trim(),
            fingerprint: f.oracle_fingerprint.trim(),
            region: (f.oracle_region || f.default_region || "").trim(),
            compartment_id: f.oracle_compartment_id.trim(),
            key_pem: f.oracle_key_pem,
          };
        } else if (f.provider === "azure") {
          if (!f.azure_tenant_id || !f.azure_client_id || !f.azure_client_secret || !f.azure_subscription_id) {
            throw new Error("Azure 凭据必须填 tenant_id / client_id / client_secret / subscription_id");
          }
          credentials = {
            tenant_id: f.azure_tenant_id.trim(),
            client_id: f.azure_client_id.trim(),
            client_secret: f.azure_client_secret,
            subscription_id: f.azure_subscription_id.trim(),
            resource_group: (f.azure_resource_group || "CloudHelper").trim(),
          };
        } else {
          throw new Error(`${f.provider} 暂未实现`);
        }
      }
      const payload = {
        name: f.name, provider: f.provider, default_region: f.default_region,
        group_tag: f.group_tag, note: f.note, monthly_traffic_gb: f.monthly_traffic_gb,
        credit_total_usd: Number(f.credit_total_usd) || 0,
        credit_used_usd: Number(f.credit_used_usd) || 0,
        credit_expires_at: f.credit_expires_at || null,
        credentials,
      };
      if (isEdit) await api.put(`/accounts/${account!.id}`, payload);
      else await api.post("/accounts", payload);
    },
    onSuccess: onClose,
  });

  return (
    <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-sm flex items-center justify-center z-10 p-4">
      <div className="glass w-[920px] max-h-[90vh] overflow-y-auto !p-0">
        <div className="px-6 py-4 border-b border-white/40">
          <h3 className="text-lg font-bold gradient-text">{isEdit ? `编辑账号 #${account!.id}` : "添加云账号"}</h3>
        </div>
        <div className="grid grid-cols-[1fr_320px]">
          <div className="p-6 space-y-4 border-r border-white/40">
          <div className="grid grid-cols-3 gap-3">
            <div className="col-span-2"><label className="label">名称</label>
              <input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></div>
            <div><label className="label">类型</label>
              <select className="input" value={f.provider} disabled={isEdit}
                      onChange={(e) => onProviderChange(e.target.value as Provider)}>
                <option value="aws">AWS</option>
                <option value="gcp">GCP</option>
                <option value="oracle">Oracle</option>
                <option value="azure">Azure</option>
              </select></div>
          </div>
          <RegionField provider={f.provider} value={f.default_region} onChange={(v) => setF({ ...f, default_region: v })} />
          <div className="grid grid-cols-3 gap-3">
            <div className="col-span-2"><label className="label">分组（如 free-tier-2026）</label>
              <input className="input" value={f.group_tag} onChange={(e) => setF({ ...f, group_tag: e.target.value })} /></div>
            <div><label className="label">免费流量 GB/月</label>
              <input type="number" className="input" min={0} step={1}
                value={f.monthly_traffic_gb} onChange={(e) => setF({ ...f, monthly_traffic_gb: Number(e.target.value) })} />
              <div className="text-[10px] text-slate-400 mt-1">
                参考：AWS 100 · GCP 200 · Oracle 10240 · Azure 15
              </div></div>
          </div>
          <div><label className="label">备注</label>
            <input className="input" placeholder="如：到期 2026-12, 邮箱 xxx@" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></div>

          <div className="rounded-lg border border-indigo-200/50 bg-indigo-50/30 p-3 space-y-2">
            <div className="text-[11px] font-semibold text-indigo-700 uppercase tracking-wider">💰 赠金 / 试用余额（手动维护）</div>
            <div className="grid grid-cols-3 gap-3">
              <div><label className="label">赠金总额 (USD)</label>
                <input type="number" className="input" min={0} step={1}
                  value={f.credit_total_usd} onChange={(e) => setF({ ...f, credit_total_usd: Number(e.target.value) })} /></div>
              <div><label className="label">已用 (USD)</label>
                <input type="number" className="input" min={0} step={0.01}
                  value={f.credit_used_usd} onChange={(e) => setF({ ...f, credit_used_usd: Number(e.target.value) })} /></div>
              <div><label className="label">到期日</label>
                <input type="date" className="input"
                  value={f.credit_expires_at} onChange={(e) => setF({ ...f, credit_expires_at: e.target.value })} /></div>
            </div>
            <div className="text-[11px] text-slate-500">
              新账号 GCP 一般 $300 / 90 天，AWS $300 / 1 年，Oracle $300 / 30 天。手动从 Console 看一眼填进来即可。
            </div>
          </div>

          {isEdit && (
            <label className="text-sm flex items-center gap-2 bg-slate-50 rounded-md px-3 py-2 border border-slate-200">
              <input type="checkbox" checked={keepCreds} onChange={(e) => setKeepCreds(e.target.checked)} />
              保留原凭据不修改
            </label>
          )}

          {(!keepCreds || !isEdit) && f.provider === "aws" && (
            <>
              <div><label className="label">Access Key ID</label>
                <input className="input font-mono" value={f.aws_access_key_id} onChange={(e) => setF({ ...f, aws_access_key_id: e.target.value })} /></div>
              <div><label className="label">Secret Access Key</label>
                <input type="password" className="input font-mono" value={f.aws_secret_access_key} onChange={(e) => setF({ ...f, aws_secret_access_key: e.target.value })} /></div>
            </>
          )}
          {(!keepCreds || !isEdit) && f.provider === "gcp" && (
            <div><label className="label">Service Account JSON</label>
              <textarea className="input h-40 font-mono text-xs" value={f.gcp_sa_json} onChange={(e) => setF({ ...f, gcp_sa_json: e.target.value })} /></div>
          )}
          {(!keepCreds || !isEdit) && f.provider === "oracle" && (
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <div><label className="label">Tenancy OCID</label>
                  <input className="input font-mono text-xs" value={f.oracle_tenancy} onChange={(e) => setF({ ...f, oracle_tenancy: e.target.value })} placeholder="ocid1.tenancy.oc1.." /></div>
                <div><label className="label">User OCID</label>
                  <input className="input font-mono text-xs" value={f.oracle_user} onChange={(e) => setF({ ...f, oracle_user: e.target.value })} placeholder="ocid1.user.oc1.." /></div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div><label className="label">API Key Fingerprint</label>
                  <input className="input font-mono text-xs" value={f.oracle_fingerprint} onChange={(e) => setF({ ...f, oracle_fingerprint: e.target.value })} placeholder="aa:bb:cc:..." /></div>
                <div><label className="label">默认 Region（可选，覆盖账号默认）</label>
                  <input className="input font-mono text-xs" value={f.oracle_region} onChange={(e) => setF({ ...f, oracle_region: e.target.value })} placeholder="ap-singapore-1" /></div>
              </div>
              <div><label className="label">Compartment OCID（推荐，省去自动探测）</label>
                <input className="input font-mono text-xs" value={f.oracle_compartment_id} onChange={(e) => setF({ ...f, oracle_compartment_id: e.target.value })} placeholder="ocid1.compartment.oc1.." /></div>
              <div><label className="label">API 私钥 PEM</label>
                <textarea className="input h-32 font-mono text-xs" value={f.oracle_key_pem} onChange={(e) => setF({ ...f, oracle_key_pem: e.target.value })} placeholder="-----BEGIN PRIVATE KEY-----..." /></div>
            </div>
          )}
          {(!keepCreds || !isEdit) && f.provider === "azure" && (
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <div><label className="label">Tenant ID</label>
                  <input className="input font-mono text-xs" value={f.azure_tenant_id} onChange={(e) => setF({ ...f, azure_tenant_id: e.target.value })} placeholder="00000000-0000-0000-0000-000000000000" /></div>
                <div><label className="label">Subscription ID</label>
                  <input className="input font-mono text-xs" value={f.azure_subscription_id} onChange={(e) => setF({ ...f, azure_subscription_id: e.target.value })} placeholder="00000000-..." /></div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div><label className="label">Client ID（App Registration）</label>
                  <input className="input font-mono text-xs" value={f.azure_client_id} onChange={(e) => setF({ ...f, azure_client_id: e.target.value })} placeholder="00000000-..." /></div>
                <div><label className="label">Client Secret</label>
                  <input type="password" className="input font-mono text-xs" value={f.azure_client_secret} onChange={(e) => setF({ ...f, azure_client_secret: e.target.value })} /></div>
              </div>
              <div><label className="label">资源组（不存在会自动创建）</label>
                <input className="input font-mono text-xs" value={f.azure_resource_group} onChange={(e) => setF({ ...f, azure_resource_group: e.target.value })} placeholder="CloudHelper" /></div>
            </div>
          )}
          {save.isError && <div className="text-sm text-red-600 bg-red-50 border border-red-100 rounded-md p-2">{(save.error as Error).message}</div>}
        </div>
        <ProviderGuide provider={f.provider} />
        </div>
        <div className="px-6 py-3 border-t border-white/40 flex justify-end gap-2">
          <button className="btn" onClick={onClose}>取消</button>
          <button className="btn-primary" disabled={save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? "保存中…" : "保存"}
          </button>
        </div>
      </div>
    </div>
  );
}

function ProviderGuide({ provider }: { provider: Provider }) {
  const guides: Record<Provider, { title: string; steps: { label: string; href?: string; tip?: string }[]; perms: string[] }> = {
    aws: {
      title: "AWS 凭据获取",
      steps: [
        { label: "1. 登录 AWS Console", href: "https://console.aws.amazon.com/iam/home" },
        { label: "2. 用户 → 创建用户", tip: "建议名为 cloudhelper" },
        { label: "3. 附加策略 → AmazonEC2FullAccess + AmazonVPCFullAccess + IAMReadOnlyAccess" },
        { label: "4. 创建用户后 → 安全凭证 → 创建访问密钥" },
        { label: "5. 选 Application running outside AWS → 复制 Access key ID + Secret" },
      ],
      perms: ["AmazonEC2FullAccess", "AmazonVPCFullAccess", "（可选）AWSPriceListServiceFullAccess"],
    },
    gcp: {
      title: "GCP Service Account JSON 获取",
      steps: [
        { label: "1. Google Cloud Console", href: "https://console.cloud.google.com/iam-admin/serviceaccounts" },
        { label: "2. 选项目 → 创建服务账号", tip: "名称 cloudhelper" },
        { label: "3. 授予角色 → Compute Admin" },
        { label: "4. 完成 → 找到 SA → 密钥 → 添加密钥 → JSON" },
        { label: "5. 自动下载的 JSON 文件全部内容粘到上方文本框" },
        { label: "⚠ 必须开启 Billing 才能调用 Compute API", href: "https://console.cloud.google.com/billing" },
      ],
      perms: ["roles/compute.admin", "（可选）roles/cloudbilling.viewer"],
    },
    oracle: {
      title: "Oracle Cloud（OCI）凭据",
      steps: [
        { label: "1. 登录 OCI Console", href: "https://cloud.oracle.com/" },
        { label: "2. Profile → User Settings → API Keys → Add API Key", tip: "选 Generate API Key Pair 并下载 private key" },
        { label: "3. 复制底部 Configuration File Preview 里的 tenancy / user / fingerprint" },
        { label: "4. 默认 Region 用你订阅的某个区（如 ap-singapore-1）" },
        { label: "5. 推荐填 Compartment OCID（不填则自动列举）" },
      ],
      perms: ["Compute / Network / Identity Read+Manage in target compartment"],
    },
    azure: {
      title: "Azure 凭据（Service Principal）",
      steps: [
        { label: "1. Azure Portal → App registrations", href: "https://portal.azure.com/#blade/Microsoft_AAD_RegisteredApps/ApplicationsListBlade" },
        { label: "2. New registration → 拿到 Application (client) ID + Directory (tenant) ID" },
        { label: "3. Certificates & secrets → New client secret → 复制 Value" },
        { label: "4. Subscription → IAM → Add role assignment → Contributor 给该 SP" },
        { label: "5. Subscription ID 在订阅总览复制" },
      ],
      perms: ["Contributor on subscription", "Reader on tenant locations"],
    },
  };
  const g = guides[provider];
  return (
    <div className="p-5 bg-gradient-to-br from-indigo-50/30 to-violet-50/30 overflow-y-auto">
      <div className="text-[11px] font-semibold uppercase tracking-widest text-indigo-700 mb-3">📖 {g.title}</div>
      <ol className="space-y-2.5">
        {g.steps.map((s, i) => (
          <li key={i} className="text-xs text-slate-700">
            {s.href ? (
              <a href={s.href} target="_blank" rel="noopener noreferrer"
                className="text-indigo-600 hover:text-indigo-700 hover:underline font-medium">
                {s.label} ↗
              </a>
            ) : (
              <span>{s.label}</span>
            )}
            {s.tip && <div className="text-[11px] text-slate-500 ml-2 mt-0.5">{s.tip}</div>}
          </li>
        ))}
      </ol>
      {g.perms.length > 0 && (
        <div className="mt-5 pt-4 border-t border-indigo-100">
          <div className="text-[11px] font-semibold text-slate-600 mb-2">所需权限</div>
          <div className="space-y-1">
            {g.perms.map((p) => (
              <div key={p} className="text-[11px] text-slate-600 font-mono bg-white/50 rounded px-2 py-1">{p}</div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
