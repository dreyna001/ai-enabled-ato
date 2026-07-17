import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

const TERMINAL_INTAKE_STATUSES: Record<
  string,
  { title: string; description: string; tone: "error" | "warning" }
> = {
  invalid: {
    title: "Package intake invalid",
    description:
      "Uploaded content failed validation or extraction. Review filenames and formats, then create a new revision.",
    tone: "error",
  },
  quarantined: {
    title: "Package quarantined",
    description:
      "Malware scanning flagged this revision. Do not download or re-upload quarantined artifacts. Contact your operator.",
    tone: "error",
  },
  archived: {
    title: "Revision archived",
    description: "This revision is archived and cannot continue through intake.",
    tone: "warning",
  },
};

type TerminalIntakePanelProps = {
  status: string;
  reconciliationMessage?: string | null;
};

export function TerminalIntakePanel({
  status,
  reconciliationMessage,
}: TerminalIntakePanelProps) {
  const config = TERMINAL_INTAKE_STATUSES[status] ?? {
    title: "Intake stopped",
    description: `Revision is in terminal status "${status}".`,
    tone: "warning" as const,
  };

  return (
    <Card className="border border-border border-l-4 border-l-destructive">
      <CardHeader>
        <CardTitle className="text-base">{config.title}</CardTitle>
        <CardDescription className="flex flex-wrap items-center gap-2">
          <Badge variant="destructive">{status}</Badge>
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p>{config.description}</p>
        {reconciliationMessage ? (
          <p className="rounded-sm border border-border border-l-4 border-l-amber-500 bg-card px-3 py-2 text-foreground">
            {reconciliationMessage}
          </p>
        ) : null}
        <Button type="button" size="sm" variant="outline" onClick={() => window.location.reload()}>
          Reload page
        </Button>
      </CardContent>
    </Card>
  );
}

export function ReconciliationNotice({ message }: { message: string }) {
  return (
    <div
      role="alert"
      className="rounded-sm border border-border border-l-4 border-l-amber-500 bg-card px-4 py-3 text-sm text-foreground"
    >
      {message}
    </div>
  );
}
