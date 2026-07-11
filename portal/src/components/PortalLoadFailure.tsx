type PortalLoadFailureProps = {
  title?: string;
  message: string;
};

export function PortalLoadFailure({
  title = "Portal unavailable",
  message,
}: PortalLoadFailureProps) {
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
      <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
      <p className="text-sm text-destructive">{message}</p>
      <p className="max-w-lg text-sm text-muted-foreground">
        Reload the page or contact your operator if this persists.
      </p>
    </div>
  );
}
