import { useCallback, useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import {
  fetchReadiness,
  fetchSession,
  isCancelledRequest,
  login,
  logout,
} from "@/api/client";
import { AppLayout } from "@/components/Layout";
import { SessionBootstrapSkeleton } from "@/components/LoadingSkeletons";
import {
  LoginPage,
  WorkflowRoute,
} from "@/pages/WorkflowPage";
import type { PortalReadinessState, SessionInfo } from "@/types";
import { formatApiError } from "@/utils/formatApiError";

const INITIAL_READINESS: PortalReadinessState = {
  loaded: false,
  ready: false,
  degraded: false,
  error: null,
  checks: [],
};

export function PortalApp() {
  const [session, setSession] = useState<SessionInfo | null | undefined>(undefined);
  const [sessionError, setSessionError] = useState("");
  const [readiness, setReadiness] = useState<PortalReadinessState>(INITIAL_READINESS);

  const refreshSession = useCallback(async (signal?: AbortSignal) => {
    try {
      const value = await fetchSession({ signal });
      setSession(value);
      setSessionError("");
    } catch (err) {
      if (isCancelledRequest(err, signal)) {
        return;
      }
      setSession(null);
      setSessionError(formatApiError(err));
    }
  }, []);

  const refreshReadiness = useCallback(async (signal?: AbortSignal) => {
    try {
      const payload = await fetchReadiness({ signal });
      const checks = Object.entries(payload.checks ?? {}).map(([name, status]) => ({
        name,
        status,
      }));
      setReadiness({
        loaded: true,
        ready: payload.status === "ok",
        degraded: payload.status !== "ok",
        error: null,
        checks,
      });
    } catch (err) {
      if (isCancelledRequest(err, signal)) {
        return;
      }
      setReadiness({
        loaded: true,
        ready: false,
        degraded: true,
        error: formatApiError(err),
        checks: [],
      });
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void refreshSession(controller.signal);
    void refreshReadiness(controller.signal);
    const refreshOnFocus = () => {
      void refreshSession();
    };
    window.addEventListener("focus", refreshOnFocus);
    return () => {
      controller.abort();
      window.removeEventListener("focus", refreshOnFocus);
    };
  }, [refreshReadiness, refreshSession]);

  const handleSignOut = () => {
    void logout().then(() => refreshSession());
  };

  if (session === undefined) {
    return <SessionBootstrapSkeleton />;
  }

  if (!session) {
    return (
      <Routes>
        <Route
          path="/login"
          element={<LoginPage error={sessionError} onSignIn={login} />}
        />
        <Route path="*" element={<Navigate replace to="/login" />} />
      </Routes>
    );
  }

  return (
    <Routes>
      <Route
        element={
          <AppLayout
            readiness={readiness}
            session={session}
            onSignOut={handleSignOut}
          />
        }
      >
        <Route
          path="/workflow"
          element={
            <WorkflowRoute session={session} readiness={readiness} />
          }
        />
        <Route
          path="/workflow/systems/:systemId"
          element={
            <WorkflowRoute session={session} readiness={readiness} />
          }
        />
        <Route
          path="/workflow/systems/:systemId/revisions/:revisionId"
          element={
            <WorkflowRoute session={session} readiness={readiness} />
          }
        />
      </Route>
      <Route path="/login" element={<Navigate replace to="/workflow" />} />
      <Route path="*" element={<Navigate replace to="/workflow" />} />
    </Routes>
  );
}
