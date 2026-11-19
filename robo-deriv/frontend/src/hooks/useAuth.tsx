import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { authApi, getStoredToken, setStoredToken } from '../lib/api';
import type { User } from '../types/api';

interface AuthContextValue {
  user: User | null;
  token: string | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<User>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<User | null>;
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(() => getStoredToken());
  const [loading, setLoading] = useState<boolean>(true);

  const refreshUser = useCallback(async (): Promise<User | null> => {
    try {
      const me = await authApi.me();
      setUser(me);
      setToken(getStoredToken());
      return me;
    } catch {
      try {
        const refreshed = await authApi.refresh();
        if (refreshed?.access_token) {
          setStoredToken(refreshed.access_token);
          setToken(refreshed.access_token);
          if (refreshed.user) {
            setUser(refreshed.user);
            return refreshed.user;
          }
          const me = await authApi.me();
          setUser(me);
          return me;
        }
      } catch {
        // session expired
      }
      setStoredToken(null);
      setToken(null);
      setUser(null);
      return null;
    }
  }, []);

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const existingToken = getStoredToken();
        if (existingToken) {
          const me = await authApi.me();
          if (active) {
            setUser(me);
            setToken(getStoredToken());
          }
        } else {
          // Try refresh cookie silently
          try {
            const refreshed = await authApi.refresh();
            if (active && refreshed?.access_token) {
              setStoredToken(refreshed.access_token);
              setToken(refreshed.access_token);
              setUser(refreshed.user ?? (await authApi.me()));
            }
          } catch {
            // not logged in
          }
        }
      } catch {
        if (active) {
          setStoredToken(null);
          setToken(null);
          setUser(null);
        }
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  const login = useCallback(async (email: string, password: string): Promise<User> => {
    const res = await authApi.login(email.trim(), password);
    setStoredToken(res.access_token);
    setToken(res.access_token);
    setUser(res.user);
    return res.user;
  }, []);

  const logout = useCallback(async () => {
    try {
      await authApi.logout();
    } catch {
      // ignore network error on logout
    } finally {
      setStoredToken(null);
      setToken(null);
      setUser(null);
    }
  }, []);

  const changePassword = useCallback(async (currentPassword: string, newPassword: string) => {
    await authApi.changePassword(currentPassword, newPassword);
    const updated = await authApi.me();
    setUser(updated);
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      token,
      loading,
      login,
      logout,
      refreshUser,
      changePassword,
    }),
    [user, token, loading, login, logout, refreshUser, changePassword]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth deve ser utilizado dentro de AuthProvider');
  }
  return ctx;
}
