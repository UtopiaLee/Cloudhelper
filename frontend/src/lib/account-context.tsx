import { createContext, useContext, useEffect, useMemo, useState, ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, Account } from "./api";

interface AccountCtx {
  accounts: Account[];
  current: Account | null;
  setCurrent: (a: Account | null) => void;
  loading: boolean;
  groups: string[];
  group: string | null;
  setGroup: (g: string | null) => void;
}

const Ctx = createContext<AccountCtx>({
  accounts: [], current: null, setCurrent: () => {}, loading: false,
  groups: [], group: null, setGroup: () => {},
});

export function AccountProvider({ children }: { children: ReactNode }) {
  const [current, setCurrent] = useState<Account | null>(null);
  const [group, setGroup] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["accounts"],
    queryFn: async () => (await api.get<Account[]>("/accounts")).data,
  });
  const accounts = data || [];
  const groups = useMemo(() => {
    const s = new Set<string>();
    accounts.forEach((a) => a.group_tag && s.add(a.group_tag));
    return Array.from(s).sort();
  }, [accounts]);

  useEffect(() => {
    if (!current && accounts.length) {
      const stored = localStorage.getItem("currentAccountId");
      const found = stored ? accounts.find((a) => a.id === Number(stored)) : null;
      setCurrent(found || accounts[0]);
    }
  }, [accounts, current]);

  useEffect(() => {
    if (current) localStorage.setItem("currentAccountId", String(current.id));
  }, [current]);

  return (
    <Ctx.Provider value={{ accounts, current, setCurrent, loading: isLoading, groups, group, setGroup }}>
      {children}
    </Ctx.Provider>
  );
}

export function useAccount() {
  return useContext(Ctx);
}
