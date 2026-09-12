import {
  Bot,
  ClipboardCheck,
  Database,
  FileStack,
  FileText,
  FolderOpen,
  HelpCircle,
  LayoutDashboard,
  Network,
  Plus,
  ShieldCheck,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ContextualAgentDrawer } from "@/components/ssp-workspace/ContextualAgentDrawer";
import { useGlobalChatbot } from "@/components/ssp-workspace/GlobalChatbotProvider";
import { ControlWorkbench } from "@/components/ssp-workspace/ControlWorkbench";
import { EvidencePanel } from "@/components/ssp-workspace/EvidencePanel";
import { InformationTypesPanel } from "@/components/ssp-workspace/InformationTypesPanel";
import { QuestionsPanel } from "@/components/ssp-workspace/QuestionsPanel";
import { ReviewExportPanel } from "@/components/ssp-workspace/ReviewExportPanel";
import { SspDocumentPanel } from "@/components/ssp-workspace/SspDocumentPanel";
import { SystemDefinitionPanel } from "@/components/ssp-workspace/SystemDefinitionPanel";
import { WorkspaceOverview } from "@/components/ssp-workspace/WorkspaceOverview";
import {
  WorkspaceEmptyState,
  WorkspaceErrorState,
  WorkspaceLoadingState,
} from "@/components/ssp-workspace/WorkspaceStatePanel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type {
  AgentContext,
  SspWorkspace,
  SspWorkspaceActions,
} from "@/sspWorkspaceTypes";
import { STRUCTURED_INFORMATION_TYPES_SECTION_IDS, STRUCTURED_SYSTEM_DEFINITION_SECTION_IDS } from "@/sspWorkspaceTypes";
import { calculateSspWorkspaceMetrics } from "@/utils/sspWorkspaceMetrics";

export type WorkspaceView =
  | "overview"
  | "evidence"
  | "system-definition"
  | "information-types"
  | "ssp"
  | "controls"
  | "questions"
  | "review";

export type SspWorkspacePageProps =
  | { state: "loading" }
  | { state: "error"; message: string; onRetry?: () => void }
  | { state: "empty"; onCreateWorkspace?: () => void }
  | {
      state: "success";
      workspace: SspWorkspace;
      actions?: SspWorkspaceActions;
      availableWorkspaces?: Array<{ id: string; name: string }>;
      generationPending?: boolean;
      categorizationAnalyzePending?: boolean;
      initialView?: WorkspaceView;
      initialTargetId?: string;
      actionsBusy?: boolean;
    };

const NAV_ITEMS: Array<{
  id: WorkspaceView;
  label: string;
  icon: typeof LayoutDashboard;
}> = [
  { id: "overview", label: "Overview", icon: LayoutDashboard },
  { id: "evidence", label: "Intake & evidence", icon: FileStack },
  { id: "system-definition", label: "System definition", icon: Network },
  { id: "information-types", label: "Information types", icon: Database },
  { id: "ssp", label: "SSP document", icon: FileText },
  { id: "controls", label: "Controls", icon: ShieldCheck },
  { id: "questions", label: "Questions", icon: HelpCircle },
  { id: "review", label: "Review & export", icon: ClipboardCheck },
];

function countForView(
  view: WorkspaceView,
  metrics: ReturnType<typeof calculateSspWorkspaceMetrics>,
): string | null {
  if (view === "evidence") return String(metrics.evidence);
  if (view === "information-types") {
    return metrics.informationTypesCount > 0
      ? String(metrics.informationTypesCount)
      : null;
  }
  if (view === "ssp") return `${metrics.sspCompletion}%`;
  if (view === "controls") return String(metrics.selectedControls);
  if (view === "questions") return String(metrics.openQuestions);
  return null;
}

function SspWorkspaceSuccess({
  workspace,
  actions = {},
  availableWorkspaces = [],
  generationPending = false,
  categorizationAnalyzePending = false,
  initialView = "overview",
  initialTargetId,
  actionsBusy = false,
}: {
  workspace: SspWorkspace;
  actions?: SspWorkspaceActions;
  availableWorkspaces?: Array<{ id: string; name: string }>;
  generationPending?: boolean;
  categorizationAnalyzePending?: boolean;
  initialView?: WorkspaceView;
  initialTargetId?: string;
  actionsBusy?: boolean;
}) {
  const [view, setView] = useState<WorkspaceView>(initialView);
  const documentSections = workspace.sections.filter(
    (section) =>
      !STRUCTURED_SYSTEM_DEFINITION_SECTION_IDS.has(section.id) &&
      !STRUCTURED_INFORMATION_TYPES_SECTION_IDS.has(section.id),
  );
  const initialSection = documentSections.find(
    (section) => section.id === initialTargetId,
  );
  const [selectedSectionId, setSelectedSectionId] = useState<string | null>(
    initialSection?.id ?? documentSections[0]?.id ?? null,
  );
  const initialControl = workspace.controls.find(
    (control) => control.id === initialTargetId,
  );
  const [selectedControlId, setSelectedControlId] = useState<string | null>(
    initialControl?.id ??
      workspace.controls.find(
        (control) => control.state === "partial" || control.state === "empty",
      )?.id ??
      workspace.controls[0]?.id ??
      null,
  );
  const [agentContext, setAgentContext] = useState<AgentContext | null>(null);
  const agentTriggerRef = useRef<HTMLButtonElement>(null);
  const chatbot = useGlobalChatbot();
  const actionsRef = useRef(actions);
  actionsRef.current = actions;
  const patchStateSignature = workspace.patches
    .map((patch) => `${patch.id}:${patch.state}:${patch.summary}:${patch.targetLabels.join(",")}`)
    .join("|");
  useEffect(() => {
    if (!chatbot) return;
    chatbot.registerWorkspace(workspace, actionsRef.current);
  }, [
    actions,
    chatbot,
    chatbot?.identityKey,
    patchStateSignature,
    workspace.id,
    workspace.name,
    workspace.revisionId,
  ]);
  const openAgent = (context: AgentContext) => {
    if (chatbot) {
      chatbot.openChat({ context, focus: context.label });
      return;
    }
    setAgentContext(context);
  };
  const metrics = calculateSspWorkspaceMetrics(workspace);
  const currentViewLabel =
    NAV_ITEMS.find((item) => item.id === view)?.label ?? "Workspace";
  const evidenceRemovalAllowed =
    workspace.sections.every((section) => section.state === "empty") &&
    workspace.controls.every((control) => control.state === "empty") &&
    workspace.questions.length === 0 &&
    workspace.patches.length === 0;

  return (
    <div className="min-h-full bg-background">
      <header className="border-b bg-card">
        <div className="mx-auto flex max-w-[96rem] flex-wrap items-center justify-between gap-4 px-4 py-4 sm:px-6">
          <div>
            <p className="text-xs text-muted-foreground">
              Systems / {workspace.name} / {currentViewLabel}
            </p>
            <div className="mt-1 flex flex-wrap items-center gap-2">
              <h1 className="text-xl font-semibold">{workspace.name}</h1>
              <Badge variant="outline">{workspace.profile.baseline}</Badge>
              <Badge variant={metrics.approved ? "success" : "secondary"}>
                {metrics.approved ? "ISSO approved" : "Working revision"}
              </Badge>
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              {workspace.profile.name} · {workspace.profile.version} · Revision{" "}
              <span className="font-mono">{workspace.revisionId}</span>
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {availableWorkspaces.length > 0 && actions.onOpenWorkspace ? (
              <label className="flex items-center gap-2 text-xs text-muted-foreground">
                <FolderOpen className="size-4" aria-hidden="true" />
                <span className="sr-only">Open system</span>
                <select
                  aria-label="Open system"
                  className="h-9 max-w-64 rounded-md border bg-background px-3 text-sm text-foreground"
                  value={workspace.id}
                  onChange={(event) =>
                    actions.onOpenWorkspace?.(event.target.value)
                  }
                >
                  {availableWorkspaces.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            {actions.onNewWorkspace ? (
              <Button type="button" variant="outline" onClick={actions.onNewWorkspace}>
                <Plus aria-hidden="true" />
                New system
              </Button>
            ) : null}
            <Button
              ref={agentTriggerRef}
              type="button"
              variant="outline"
              onClick={() =>
                openAgent({
                  targetType: "workspace",
                  targetId: workspace.id,
                  label: workspace.name,
                })
              }
            >
              <Bot aria-hidden="true" />
              Ask agent
            </Button>
          </div>
        </div>
      </header>

      <div className="mx-auto grid max-w-[96rem] gap-4 px-4 py-4 sm:px-6 lg:grid-cols-[13rem_minmax(0,1fr)]">
        <nav
          className="flex gap-1 overflow-x-auto lg:flex-col"
          aria-label="SSP workspace"
        >
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            const count = countForView(item.id, metrics);
            return (
              <button
                key={item.id}
                type="button"
                className={cn(
                  "flex shrink-0 items-center gap-2 rounded-sm px-3 py-2 text-left text-sm hover:bg-muted lg:w-full",
                  view === item.id &&
                    "bg-primary text-primary-foreground hover:bg-primary/90",
                )}
                aria-current={view === item.id ? "page" : undefined}
                onClick={() => setView(item.id)}
              >
                <Icon className="size-4" aria-hidden="true" />
                <span className="flex-1">{item.label}</span>
                {count ? (
                  <span
                    className={cn(
                      "rounded-sm bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground",
                      view === item.id &&
                        "bg-primary-foreground/15 text-primary-foreground",
                    )}
                  >
                    {count}
                  </span>
                ) : null}
              </button>
            );
          })}
        </nav>

        <main className="min-w-0">
          {view === "overview" ? (
            <WorkspaceOverview
              workspace={workspace}
              metrics={metrics}
              onGenerate={actions.onGenerate}
              generationPending={generationPending}
              onNavigate={setView}
              onOpenAgent={openAgent}
              onSaveCategorization={actions.onSaveCategorization}
              onAnalyzeCategorization={actions.onAnalyzeCategorization}
              categorizationAnalyzePending={categorizationAnalyzePending}
            />
          ) : null}
          {view === "evidence" ? (
            <EvidencePanel
              evidence={workspace.evidence}
              onUpload={actions.onUploadEvidence}
              onRemove={actions.onRemoveEvidence}
              removalAllowed={evidenceRemovalAllowed}
            />
          ) : null}
          {view === "system-definition" ? (
            <SystemDefinitionPanel
              systemDefinition={workspace.systemDefinition}
              evidence={workspace.evidence}
              onSave={actions.onSaveSystemDefinition}
              onAnalyzeDiagram={actions.onAnalyzeDiagram}
              analyzeBusy={actionsBusy}
            />
          ) : null}
          {view === "information-types" ? (
            <InformationTypesPanel
              informationTypes={workspace.informationTypes}
              evidence={workspace.evidence}
              onSave={actions.onSaveInformationTypes}
            />
          ) : null}
          {view === "ssp" ? (
            <SspDocumentPanel
              sections={documentSections}
              selectedSectionId={selectedSectionId}
              onSelectSection={setSelectedSectionId}
              onSave={actions.onSaveSection}
              onOpenAgent={openAgent}
            />
          ) : null}
          {view === "controls" ? (
            <ControlWorkbench
              controls={workspace.controls}
              controlResponse={workspace.controlResponse}
              selectedControlId={selectedControlId}
              onSelectControl={setSelectedControlId}
              onSave={actions.onSaveControl}
              onOpenAgent={openAgent}
            />
          ) : null}
          {view === "questions" ? (
            <QuestionsPanel
              questions={workspace.questions}
              sections={workspace.sections}
              onAnswer={actions.onAnswerQuestion}
            />
          ) : null}
          {view === "review" ? (
            <ReviewExportPanel
              workspace={workspace}
              metrics={metrics}
              actionsBusy={actionsBusy}
              onApprove={actions.onApprove}
              onExport={actions.onExport}
              onUploadAgencyTemplate={actions.onUploadAgencyTemplate}
              onPreviewAgencyRender={actions.onPreviewAgencyRender}
              onApproveAgencyRender={actions.onApproveAgencyRender}
              onRejectAgencyRender={actions.onRejectAgencyRender}
              onDownloadAgencyRender={actions.onDownloadAgencyRender}
            />
          ) : null}
        </main>
      </div>

      {!chatbot && agentContext ? (
        <ContextualAgentDrawer
          context={agentContext}
          patches={workspace.patches}
          restoreFocusRef={agentTriggerRef}
          onClose={() => setAgentContext(null)}
          onAskAgent={actions.onAskAgent}
          onApplyPatch={actions.onApplyPatch}
          onRejectPatch={actions.onRejectPatch}
        />
      ) : null}
    </div>
  );
}

export function SspWorkspacePage(props: SspWorkspacePageProps) {
  if (props.state === "loading") {
    return (
      <div className="mx-auto max-w-[96rem] p-4 sm:p-6">
        <WorkspaceLoadingState />
      </div>
    );
  }
  if (props.state === "error") {
    return (
      <div className="mx-auto max-w-[96rem] p-4 sm:p-6">
        <WorkspaceErrorState message={props.message} onRetry={props.onRetry} />
      </div>
    );
  }
  if (props.state === "empty") {
    return (
      <div className="mx-auto max-w-[96rem] p-4 sm:p-6">
        <WorkspaceEmptyState onCreateWorkspace={props.onCreateWorkspace} />
      </div>
    );
  }
  return (
    <SspWorkspaceSuccess
      key={`${props.workspace.id}:${props.initialView ?? "overview"}`}
      workspace={props.workspace}
      actions={props.actions}
      availableWorkspaces={props.availableWorkspaces}
      generationPending={props.generationPending}
      categorizationAnalyzePending={props.categorizationAnalyzePending}
      initialView={props.initialView}
      initialTargetId={props.initialTargetId}
      actionsBusy={props.actionsBusy}
    />
  );
}
