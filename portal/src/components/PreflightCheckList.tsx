import { resolvePreflightCheck } from "@/utils/preflightLabels";

type PreflightCheckListProps = {
  title: string;
  codes: string[];
  checkMessages?: Map<string, string>;
  tone?: "blocker" | "warning";
};

export function PreflightCheckList({
  title,
  codes,
  checkMessages,
  tone = "blocker",
}: PreflightCheckListProps) {
  if (codes.length === 0) {
    return null;
  }

  const borderClass =
    tone === "warning"
      ? "border-l-amber-500"
      : "border-l-destructive";

  return (
    <div className="space-y-2">
      <p className="font-medium">{title}</p>
      <ul className="space-y-2">
        {codes.map((code) => {
          const info = resolvePreflightCheck(code, checkMessages?.get(code));
          return (
            <li
              key={code}
              className={`rounded-sm border border-border ${borderClass} border-l-4 bg-card px-3 py-2`}
            >
              <p className="font-medium text-foreground">{info.title}</p>
              <p className="mt-1 text-muted-foreground">{info.description}</p>
              {info.action ? (
                <p className="mt-2 text-foreground">{info.action}</p>
              ) : null}
              <p className="mt-2 font-mono text-xs text-muted-foreground">{code}</p>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function buildPreflightCheckMessageMap(
  checks: Array<{ check_id: string; message: string }> | undefined,
): Map<string, string> {
  const map = new Map<string, string>();
  for (const check of checks ?? []) {
    map.set(check.check_id, check.message);
  }
  return map;
}
