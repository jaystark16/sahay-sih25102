import { AuthProvider, useAuth } from '../auth/AuthContext';
import { ToastProvider } from '../components/ui/Toast';
import { LoadingScreen } from '../components/ui/States';
import { ChangePasswordPage, LoginPage } from '../pages/LoginPage';
import { AppShell } from './AppShell';

/**
 * Root composition: providers, then the one routing decision the app makes.
 */
export default function App() {
  return (
    <AuthProvider>
      <ToastProvider>
        <Gate />
      </ToastProvider>
    </AuthProvider>
  );
}

function Gate() {
  const { user, checking } = useAuth();

  if (checking) return <LoadingScreen />;
  if (!user) return <LoginPage />;

  // A seeded account carries must_change_password, and the API refuses every
  // other route until it is cleared. Sending the user straight to the change
  // screen is what stops them signing in successfully and then meeting "set a
  // new password" on every page with no way to do it.
  if (user.must_change_password) return <ChangePasswordPage />;

  return <AppShell />;
}
