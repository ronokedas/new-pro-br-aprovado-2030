import React from 'react';
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import { Layout } from './components/Layout';
import { AuthProvider, useAuth } from './hooks/useAuth';
import { Admin } from './pages/Admin';
import { BotConfig } from './pages/BotConfig';
import { BtcRadarPage } from './pages/BtcRadarPage';
import { Dashboard } from './pages/Dashboard';
import { DerivCallback } from './pages/DerivCallback';
import { DerivConnect } from './pages/DerivConnect';
import { Login } from './pages/Login';
import { Reports } from './pages/Reports';

const FullScreenLoader: React.FC = () => (
  <div className="min-h-screen bg-slate-950 bg-quant-grid flex items-center justify-center">
    <div className="text-center space-y-3">
      <Loader2 className="mx-auto h-8 w-8 animate-spin text-emerald-400" />
      <p className="text-xs text-slate-400">Carregando sessão...</p>
    </div>
  </div>
);

const ProtectedRoute: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (loading) return <FullScreenLoader />;
  if (!user) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return <>{children}</>;
};

const AdminRoute: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { user, loading } = useAuth();
  if (loading) return <FullScreenLoader />;
  if (!user) return <Navigate to="/login" replace />;
  if (user.role !== 'ADMIN') return <Navigate to="/dashboard" replace />;
  return <>{children}</>;
};

export const App: React.FC = () => {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route
            path="/auth/deriv/callback"
            element={
              <ProtectedRoute>
                <DerivCallback />
              </ProtectedRoute>
            }
          />

          <Route
            element={
              <ProtectedRoute>
                <Layout />
              </ProtectedRoute>
            }
          >
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/btc-radar" element={<BtcRadarPage />} />
            <Route path="/bot-config" element={<BotConfig />} />
            <Route path="/deriv-connect" element={<DerivConnect />} />
            <Route path="/reports" element={<Reports />} />
            <Route
              path="/admin"
              element={
                <AdminRoute>
                  <Admin />
                </AdminRoute>
              }
            />
          </Route>

          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
};

export default App;
