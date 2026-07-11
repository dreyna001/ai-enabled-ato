import { BrowserRouter } from "react-router-dom";
import { AppErrorBoundary } from "@/components/AppErrorBoundary";
import { PortalApp } from "./PortalApp";

export default function App() {
  return (
    <AppErrorBoundary>
      <BrowserRouter>
        <PortalApp />
      </BrowserRouter>
    </AppErrorBoundary>
  );
}
