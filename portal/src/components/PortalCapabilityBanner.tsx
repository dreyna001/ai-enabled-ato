import type { PortalReadinessState } from "@/types";

type PortalCapabilityBannerProps = {
  readiness: PortalReadinessState;
};

function buildNotices(readiness: PortalReadinessState): string[] {
  const notices: string[] = [];

  if (!readiness.loaded && !readiness.error) {
    notices.push("Checking API readiness…");
    return notices;
  }

  if (readiness.error) {
    notices.push(readiness.error);
    return notices;
  }

  if (readiness.degraded) {
    notices.push(
      "API readiness is degraded. Draft authority review (HS-001) or another readiness check may still be open.",
    );
    const degradedChecks = readiness.checks.filter((check) => check.status !== "ok");
    for (const check of degradedChecks) {
      notices.push(`${check.name}: ${check.status}`);
    }
  }

  return notices;
}

export function PortalCapabilityBanner({ readiness }: PortalCapabilityBannerProps) {
  const notices = buildNotices(readiness);
  if (!notices.length) {
    return null;
  }

  const isBlocking = Boolean(readiness.error || readiness.degraded);

  return (
    <div
      className={
        isBlocking
          ? "border-b border-amber-500/40 bg-amber-500/10 px-4 py-2.5 text-sm text-amber-50"
          : "border-b border-border/60 bg-muted/40 px-4 py-2.5 text-sm text-muted-foreground"
      }
      role="status"
    >
      <ul className="mx-auto flex w-full max-w-6xl list-disc flex-col gap-1 pl-5">
        {notices.map((notice) => (
          <li key={notice}>{notice}</li>
        ))}
      </ul>
    </div>
  );
}
