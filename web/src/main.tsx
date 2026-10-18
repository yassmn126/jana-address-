import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { AssistantProvider } from "./state/assistant";
import { ServiceProvider } from "./state/service";
import { SessionProvider } from "./state/session";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <AssistantProvider>
        <ServiceProvider>
          <SessionProvider>
            <App />
          </SessionProvider>
        </ServiceProvider>
      </AssistantProvider>
    </BrowserRouter>
  </React.StrictMode>
);
