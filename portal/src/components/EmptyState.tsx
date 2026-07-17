import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type EmptyStateAction =
  | {
      label: string;
      onClick: () => void;
    }
  | {
      label: string;
      to: string;
    };

export type EmptyStateProps = {
  title: string;
  description: string;
  action?: EmptyStateAction;
  className?: string;
  size?: "sm" | "md";
};

export function EmptyState({
  title,
  description,
  action,
  className,
  size = "md",
}: EmptyStateProps) {
  const isSmall = size === "sm";

  return (
    <div
      className={cn(
        "rounded-sm border border-dashed border-border/70 bg-muted/20",
        isSmall ? "px-3 py-3" : "px-4 py-5",
        className,
      )}
    >
      <p
        className={cn(
          "font-medium text-foreground",
          isSmall ? "text-xs" : "text-sm",
        )}
      >
        {title}
      </p>
      <p
        className={cn(
          "mt-1 text-muted-foreground",
          isSmall ? "text-xs leading-relaxed" : "text-sm leading-relaxed",
        )}
      >
        {description}
      </p>
      {action ? (
        <div className="mt-3">
          {"to" in action ? (
            <Button asChild size={isSmall ? "sm" : "default"} variant="outline">
              <Link to={action.to}>{action.label}</Link>
            </Button>
          ) : (
            <Button
              size={isSmall ? "sm" : "default"}
              type="button"
              variant="outline"
              onClick={action.onClick}
            >
              {action.label}
            </Button>
          )}
        </div>
      ) : null}
    </div>
  );
}
