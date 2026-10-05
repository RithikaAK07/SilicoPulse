"use client";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { USER_KEY, TOKEN_KEY, api, post, type DatasetStatus, type User } from "@/lib/api";

interface AuthState {
  user: User | null;
  ready: boolean;
  dataset: DatasetStatus | null;
  login: (email: string, password: string) => Promise<User>;
  logout: () => void;
  can: (permission: "read" | "upload" | "generate") => boolean;
  refreshDataset: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

function readStored(): User | null {
  try {
    const raw = window.localStorage.getItem(USER_KEY);
    return raw && window.localStorage.getItem(TOKEN_KEY) ? (JSON.parse(raw) as User) : null;
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const qc = useQueryClient();
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);
  const [dataset, setDataset] = useState<DatasetStatus | null>(null);
  const version = useRef<number | null>(null);

  const refreshDataset = useCallback(async () => {
    try {
      const d = await api<DatasetStatus>("/api/dataset/status");
      // a different active dataset invalidates every cached analytics view
      if (version.current !== null && version.current !== d.version) await qc.invalidateQueries();
      version.current = d.version;
      setDataset(d);
    } catch {
      /* 401s are handled globally by the API client */
    }
  }, [qc]);

  // restore + validate a persisted session on load
  useEffect(() => {
    const stored = readStored();
    if (!stored) {
      setReady(true);
      return;
    }
    setUser(stored);
    api<User>("/api/me")
      .then((u) => {
        setUser(u);
        window.localStorage.setItem(USER_KEY, JSON.stringify(u));
        return refreshDataset();
      })
      .catch(() => setUser(null))
      .finally(() => setReady(true));
  }, [refreshDataset]);

  const login = useCallback(
    async (email: string, password: string) => {
      const res = await post<{ access_token: string; user: User }>("/api/login", { email, password });
      window.localStorage.setItem(TOKEN_KEY, res.access_token);
      window.localStorage.setItem(USER_KEY, JSON.stringify(res.user));
      qc.clear();
      setUser(res.user);
      await refreshDataset();
      return res.user;
    },
    [qc, refreshDataset],
  );

  const logout = useCallback(() => {
    try {
      window.localStorage.removeItem(TOKEN_KEY);
      window.localStorage.removeItem(USER_KEY);
    } catch {}
    qc.clear();
    version.current = null;
    setUser(null);
    setDataset(null);
    window.location.href = "/login";
  }, [qc]);

  const can = useCallback((p: "read" | "upload" | "generate") => !!user?.permissions.includes(p), [user]);

  return <AuthContext.Provider value={{ user, ready, dataset, login, logout, can, refreshDataset }}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
