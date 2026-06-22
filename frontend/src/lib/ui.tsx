import { useMemo, useState } from "react";
import { useAccount } from "./account-context";

export function AccountPicker() {
  const { accounts, current, setCurrent, group, setGroup, groups } = useAccount();
  const [search, setSearch] = useState("");
  const filtered = useMemo(() => {
    let list = accounts;
    if (group) list = list.filter((a) => a.group_tag === group);
    if (search) {
      const s = search.toLowerCase();
      list = list.filter((a) =>
        a.name.toLowerCase().includes(s) ||
        a.note.toLowerCase().includes(s),
      );
    }
    return list;
  }, [accounts, group, search]);

  if (!accounts.length) {
    return <div className="text-sm text-slate-500">尚未配置账户，请先到 "账户" 页添加。</div>;
  }
  return (
    <div className="flex items-center gap-2 mb-4 flex-wrap">
      <span className="text-sm text-slate-600">分组：</span>
      <select className="input w-32" value={group ?? ""} onChange={(e) => setGroup(e.target.value || null)}>
        <option value="">全部</option>
        {groups.map((g) => <option key={g} value={g}>{g}</option>)}
      </select>
      <input className="input w-48" placeholder="搜索账户" value={search} onChange={(e) => setSearch(e.target.value)} />
      <span className="text-sm text-slate-600">账户：</span>
      <select
        className="input w-72"
        value={current?.id ?? ""}
        onChange={(e) => {
          const a = accounts.find((x) => x.id === Number(e.target.value));
          if (a) setCurrent(a);
        }}
      >
        {filtered.map((a) => (
          <option key={a.id} value={a.id}>
            {a.provider.toUpperCase()} · {a.name}
            {a.group_tag ? ` [${a.group_tag}]` : ""}
          </option>
        ))}
      </select>
      <span className="text-xs text-slate-400">{filtered.length}/{accounts.length}</span>
    </div>
  );
}

export function StateBadge({ state }: { state: string }) {
  const cls =
    state === "running" ? "tag tag-running"
      : state.startsWith("stop") ? "tag tag-stopped"
      : state === "pending" || state === "starting" ? "tag tag-pending"
      : "tag tag-error";
  return <span className={cls}>{state}</span>;
}

export function fmtBytes(g: number) {
  if (g < 1) return `${(g * 1024).toFixed(1)} MB`;
  return `${g.toFixed(2)} GB`;
}
