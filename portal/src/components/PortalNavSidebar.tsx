import { ClipboardList, LogOut, Shield } from "lucide-react";
import type { ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { SessionInfo } from "@/types";

export function PortalNavHeader() {
  return (
    <div className="shrink-0 px-3 py-3">
      <Link
        className="block px-2 text-sm font-semibold tracking-tight text-sidebar-foreground"
        to="/workflow"
      >
        ATO Evidence Analysis Portal
      </Link>
      <nav aria-label="Primary" className="mt-3 flex flex-col gap-1">
        <NavLink
          className={({ isActive }) =>
            cn(
              "rounded-sm px-2.5 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
              isActive && "border border-link bg-sidebar-accent text-sidebar-accent-foreground",
            )
          }
          to="/workflow"
        >
          <span className="inline-flex items-center gap-1">
            <ClipboardList className="size-3" />
            Package Workflow
          </span>
        </NavLink>
      </nav>
    </div>
  );
}

type PortalNavSidebarProps = {
  session: SessionInfo;
  onSignOut: () => void;
  footer?: ReactNode;
};

export function PortalNavSidebar({
  session,
  onSignOut,
  footer,
}: PortalNavSidebarProps) {
  return (
    <aside
      aria-label="Portal navigation"
      className="flex h-full w-[260px] shrink-0 flex-col overflow-hidden border-r border-sidebar-border bg-sidebar text-sidebar-foreground"
    >
      <PortalNavHeader />
      <div className="px-4 py-3 text-xs text-muted-foreground">
        <div className="inline-flex items-center gap-1 font-medium text-sidebar-foreground">
          <Shield className="size-3" />
          {session.actor_id}
        </div>
      </div>
      <div className="min-h-0 flex-1" />
      <div className="shrink-0 space-y-3 px-4 py-3">
        {footer}
        <Button className="w-full" size="sm" type="button" variant="outline" onClick={onSignOut}>
          <LogOut />
          Sign Out
        </Button>
      </div>
    </aside>
  );
}
