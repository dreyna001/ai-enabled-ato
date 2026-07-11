import { Outlet } from "react-router-dom";
import { PortalCapabilityBanner } from "./PortalCapabilityBanner";
import { PortalNavSidebar } from "./PortalNavSidebar";
import type { PortalReadinessState, SessionInfo } from "@/types";

type AppLayoutProps = {
  session: SessionInfo;
  readiness: PortalReadinessState;
  onSignOut: () => void;
};

export function AppLayout({ session, readiness, onSignOut }: AppLayoutProps) {
  return (
    <div className="flex h-screen w-full overflow-hidden bg-background">
      <PortalNavSidebar session={session} onSignOut={onSignOut} />
      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        <PortalCapabilityBanner readiness={readiness} />
        <main className="portal-scrollbar min-h-0 min-w-0 flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-6xl px-6 py-8">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
