import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api } from "./api.js";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [me, setMe] = useState(null);
  const [bots, setBots] = useState([]);
  const [ready, setReady] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const data = await api("/api/auth/me");
      setMe(data.user || null);
      setBots(data.bots || []);
    } catch {
      setMe(null);
      setBots([]);
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
      body: JSON.stringify({ email, handle, password }),
    });
    setMe(data.user);
    await refresh();
    return data.user;
  }, [refresh]);

  const logout = useCallback(async () => {
    await api("/api/auth/logout", { method: "POST" });
    setMe(null);
    setBots([]);
  }, []);

  const value = useMemo(
    () => ({ me, bots, ready, refresh, login, signup, logout }),
    [me, bots, ready, refresh, login, signup, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
