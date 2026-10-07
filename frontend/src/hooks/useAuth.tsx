import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, onUnauthorized, refreshSession, setAccessToken } from "@/services/api";
import type { Me } from "@/types";

interface AuthState {
  me: Me | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  can: (permission: string) => boolean;
  isAdmin: boolean;
  isManager: boolean;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  const loadMe = useCallback(async () => {
    const profile = await api.get<Me>("/api/auth/me");
    if (profile.kind !== "user") throw new Error("Agents cannot sign in to the dashboard");
    setMe(profile);
  }, []);

  useEffect(() => {
    // Restore the session from the HttpOnly refresh cookie, if any.
    (async () => {
      try {
        if (await refreshSession()) await loadMe();
      } catch {
        setAccessToken(null);
      } finally {
        setLoading(false);
      }
    })();
    return onUnauthorized(() => {
      setAccessToken(null);
      setMe(null);
    });
  }, [loadMe]);

  const login = useCallback(
    async (username: string, password: string) => {
      const tokens = await api.post<{ access_token: string }>("/api/auth/login", { username, password });
      setAccessToken(tokens.access_token);
      await loadMe();
    },
    [loadMe],
  );

  const logout = useCallback(async () => {
    try {
      await api.post("/api/auth/logout");
    } finally {
      setAccessToken(null);
      setMe(null);
    }
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      me,
      loading,
      login,
      logout,
      can: (p: string) => !!me?.permissions.includes(p),
      isAdmin: !!me?.roles.includes("ADMIN"),
      isManager: !!me?.roles.includes("TEAM_MANAGER"),
    }),
    [me, loading, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
