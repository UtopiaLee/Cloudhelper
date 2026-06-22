import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { AccountProvider } from "./lib/account-context";
import { ToastProvider } from "./lib/toast";
import { ErrorBoundary } from "./lib/error-boundary";
import { captureKnockFromUrl } from "./lib/api";
import "./index.css";

// 启动时尝试从 ?key=xxx 抓 knock secret
captureKnockFromUrl();

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 30_000, refetchOnWindowFocus: false, retry: 1 },
    mutations: { retry: 0 },
  },
});

// 全局 unhandled promise / error 钩子 — 防止某些异常完全静默
window.addEventListener("unhandledrejection", (e) => {
  console.error("[unhandledrejection]", e.reason);
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <ErrorBoundary>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AccountProvider>
          <ToastProvider>
            <App />
          </ToastProvider>
        </AccountProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </ErrorBoundary>,
);
