import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export function LoginPage({
  error,
  onSignIn,
}: {
  error: string;
  onSignIn: () => void;
}) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-6 py-8">
      <Card className="w-full max-w-lg">
        <CardHeader>
          <CardTitle className="text-2xl">Internal SSP Drafting Portal</CardTitle>
          <CardDescription>
            Sign in with OIDC to manage system evidence, SSP content, and control
            implementation statements.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {error ? (
            <div
              className="rounded-sm border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive"
              role="alert"
            >
              {error}
            </div>
          ) : null}
          <Button type="button" onClick={onSignIn}>
            Sign in
          </Button>
          <p className="text-sm text-muted-foreground">
            Need an account? Contact your operator for OIDC access.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
