import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api } from "./api.js";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [me, setMe] = useState(null);
  const [bots, setBots] = useState([]);
  const [paper, setPaper] = useState(null);
  const [plan, setPlan] = useState("open");
  const [botLimit, setBotLimit] = useState(1);
  const [ready, setReady] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const data = await api("/api/auth/me");
      setMe(data.user || null);
      setBots(data.bots || []);
      setPaper(data.paper || null);
      setPlan(data.plan || "open");
      setBotLimit(data.botLimit || 1);
    } catch {
      setMe(null);
      setBots([]);
      setPaper(null);
      setPlan("open");
      setBotLimit(1);
    } finally {
      setReady(true);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const login = useCallback(async (email, password) => {
    const data = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    setMe(data.user);
    await refresh();
    return data.user;
  }, [refresh]);

  const signup = useCallback(async (email, handle, password) => {
    const data = await api("/api/auth/signup", {
      method: "POST",
      body: JSON.stringify({ email, handle, password, terms: true }),
    });
    setMe(data.user);
    await refresh();
    return data.user;
  }, [refresh]);

  const logout = useCallback(async () => {
    await api("/api/auth/logout", { method: "POST" });
    setMe(null);
    setBots([]);
    setPaper(null);
    setPlan("open");
    setBotLimit(1);
  }, []);

  const value = useMemo(
    () => ({ me, bots, paper, plan, botLimit, ready, refresh, login, signup, logout }),
    [me, bots, paper, plan, botLimit, ready, refresh, login, signup, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
