import { Toaster } from "@/components/ui/toaster";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import AuthWrapper from "./pages/AuthWrapper";
import BriefsList from "./pages/BriefsList";
import NewBrief from "./pages/NewBrief";
import BriefDetail from "./pages/BriefDetail";
import NotFound from "./pages/NotFound";
import './amplify';

const queryClient = new QueryClient();

const App = () => (
  <QueryClientProvider client={queryClient}>
    <TooltipProvider>
      <Toaster />
      <Sonner />
      <BrowserRouter>
        <AuthWrapper>
          <Routes>
            <Route path="/" element={<Navigate to="/briefs" replace />} />
            <Route path="/briefs" element={<BriefsList />} />
            <Route path="/new" element={<NewBrief />} />
            <Route path="/briefs/:id" element={<BriefDetail />} />
            {/* ADD ALL CUSTOM ROUTES ABOVE THE CATCH-ALL "*" ROUTE */}
            <Route path="*" element={<NotFound />} />
          </Routes>
        </AuthWrapper>
      </BrowserRouter>
    </TooltipProvider>
  </QueryClientProvider>
);

export default App;
