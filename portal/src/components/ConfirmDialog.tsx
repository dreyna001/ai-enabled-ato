import { useEffect, useRef } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

type ConfirmDialogProps = {
  open: boolean;
  title: string;
  description: string;
  error?: string | null;
  confirmLabel?: string;
  cancelLabel?: string;
  confirming?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
};

export function ConfirmDialog({
  open,
  title,
  description,
  error,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  confirming = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    cancelRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !confirming) {
        onCancel();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [confirming, onCancel, open]);

  if (!open) {
    return null;
  }

  return (
    <div
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      role="presentation"
      onClick={confirming ? undefined : onCancel}
    >
      <Card
        aria-describedby="confirm-dialog-description"
        aria-labelledby="confirm-dialog-title"
        className="w-full max-w-md border-border/80 bg-card shadow-lg"
        role="alertdialog"
        onClick={(event) => event.stopPropagation()}
      >
        <CardHeader className="pb-3">
          <CardTitle className="text-base" id="confirm-dialog-title">
            {title}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 pt-0">
          <p className="text-sm text-muted-foreground" id="confirm-dialog-description">
            {description}
          </p>
          {error ? (
            <p className="whitespace-pre-line text-sm text-destructive">{error}</p>
          ) : null}
        </CardContent>
        <div className="flex justify-end gap-2 border-t border-border/60 px-6 pb-6 pt-4">
          <Button
            ref={cancelRef}
            disabled={confirming}
            type="button"
            variant="outline"
            onClick={onCancel}
          >
            {cancelLabel}
          </Button>
          <Button
            disabled={confirming}
            type="button"
            variant="destructive"
            onClick={onConfirm}
          >
            {confirming ? "Working..." : confirmLabel}
          </Button>
        </div>
      </Card>
    </div>
  );
}
