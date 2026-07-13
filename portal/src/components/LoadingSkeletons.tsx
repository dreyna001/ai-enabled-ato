import type { ReactNode } from "react";
import { Skeleton } from "@/components/ui/skeleton";

type LoadingRegionProps = {
  label: string;
  className?: string;
  children: ReactNode;
};

function LoadingRegion({ label, className, children }: LoadingRegionProps) {
  return (
    <div aria-busy="true" aria-label={label} className={className}>
      {children}
    </div>
  );
}

export function SystemsListSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <LoadingRegion className="flex flex-wrap gap-2" label="Loading systems">
      {Array.from({ length: rows }, (_, index) => (
        <Skeleton className="h-8 w-36 rounded-md" key={`system-skeleton-${index}`} />
      ))}
    </LoadingRegion>
  );
}

export function RevisionWorkflowSkeleton() {
  return (
    <LoadingRegion className="space-y-4" label="Loading revision workflow">
      <Skeleton className="h-5 w-48" />
      <Skeleton className="h-24 w-full rounded-lg" />
      <Skeleton className="h-10 w-40 rounded-md" />
    </LoadingRegion>
  );
}

export function MatrixTableSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <LoadingRegion className="overflow-x-auto rounded-md border" label="Loading matrix">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-border bg-muted/50">
            {["Item", "Status", "Summary"].map((heading) => (
              <th className="px-4 py-2 text-left font-medium" key={heading}>
                {heading}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: rows }, (_, index) => (
            <tr className="border-b border-border/60" key={`matrix-skeleton-${index}`}>
              <td className="px-4 py-3">
                <Skeleton className="h-4 w-16" />
              </td>
              <td className="px-4 py-3">
                <Skeleton className="h-6 w-28 rounded-sm" />
              </td>
              <td className="px-4 py-3">
                <Skeleton className="h-4 w-full" />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </LoadingRegion>
  );
}

export function SessionBootstrapSkeleton() {
  return (
    <LoadingRegion
      className="flex min-h-screen items-center justify-center"
      label="Loading session"
    >
      <div className="space-y-3 text-center">
        <Skeleton className="mx-auto h-6 w-48" />
        <Skeleton className="mx-auto h-4 w-64" />
      </div>
    </LoadingRegion>
  );
}
