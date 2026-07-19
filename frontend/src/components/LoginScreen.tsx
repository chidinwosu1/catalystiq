import { useState } from "react";
import { Loader2, Lock } from "lucide-react";
import { ApiError, login } from "../lib/api";
import Logo from "./Logo";

interface LoginScreenProps {
  onSuccess: () => void;
}

export default function LoginScreen({ onSuccess }: LoginScreenProps) {
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!password || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await login(password);
      onSuccess();
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 401
          ? "Incorrect password."
          : err instanceof ApiError
            ? err.message
            : "Could not sign in."
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-6">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm rounded-2xl border border-border bg-surface p-6 shadow-lg"
      >
        <div className="mb-5 flex flex-col items-center gap-3 text-center">
          <Logo size="md" />
          <div>
            <h1 className="text-lg font-semibold text-ink-primary">Sign in</h1>
            <p className="mt-1 text-sm text-ink-secondary">
              Enter the workspace password to continue.
            </p>
          </div>
        </div>

        <label className="block text-xs text-ink-muted">
          Password
          <div className="mt-1 flex items-center gap-2 rounded-lg border border-border bg-surface-2 px-3 py-2 focus-within:border-brand-blue/50">
            <Lock size={14} className="shrink-0 text-ink-muted" />
            <input
              type="password"
              autoFocus
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full bg-transparent text-sm text-ink-primary focus:outline-none"
              placeholder="••••••••"
            />
          </div>
        </label>

        {error && (
          <p className="mt-3 text-xs text-status-critical">{error}</p>
        )}

        <button
          type="submit"
          disabled={!password || submitting}
          className="mt-5 flex w-full items-center justify-center gap-2 rounded-lg bg-brand-blue px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-40"
        >
          {submitting && <Loader2 size={14} className="animate-spin" />}
          Sign in
        </button>
      </form>
    </div>
  );
}
