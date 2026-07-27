import { AlertTriangle, FolderPlus, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

export function WorkspaceLoadingState() {
  return (
    <div className="space-y-4" aria-busy="true" aria-label="Loading SSP workspace">
      <Skeleton className="h-16 w-full" />
      <div className="grid gap-4 md:grid-cols-3">
        <Skeleton className="h-28" />
        <Skeleton className="h-28" />
        <Skeleton className="h-28" />
      </div>
      <Skeleton className="h-80 w-full" />
    </div>
  );
}

export function WorkspaceErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <Card className="border-destructive/40">
      <CardContent className="flex min-h-64 flex-col items-center justify-center gap-3 text-center">
        <AlertTriangle className="size-8 text-destructive" aria-hidden="true" />
        <div>
          <h2 className="font-semibold">SSP workspace unavailable</h2>
          <p className="mt-1 max-w-lg text-sm text-muted-foreground">{message}</p>
        </div>
        {onRetry ? (
          <Button type="button" variant="outline" onClick={onRetry}>
            <RefreshCw aria-hidden="true" />
            Retry
          </Button>
        ) : null}
      </CardContent>
    </Card>
  );
}

export function WorkspaceEmptyState({
  onCreateWorkspace,
}: {
  onCreateWorkspace?: () => void;
}) {
  return (
    <Card>
      <CardContent className="flex min-h-72 flex-col items-center justify-center gap-3 text-center">
        <FolderPlus className="size-9 text-muted-foreground" aria-hidden="true" />
        <div>
          <h2 className="font-semibold">No system workspace</h2>
          <p className="mt-1 max-w-lg text-sm text-muted-foreground">
            Create a workspace to collect evidence, generate the SSP, and draft
            control implementation statements.
          </p>
        </div>
        {onCreateWorkspace ? (
          <Button type="button" onClick={onCreateWorkspace}>
            Create system workspace
          </Button>
        ) : null}
      </CardContent>
    </Card>
  );
}
