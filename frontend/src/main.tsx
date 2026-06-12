import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App as AntApp, ConfigProvider } from "antd";
import trTR from "antd/locale/tr_TR";
import dayjs from "dayjs";
import "dayjs/locale/tr";
import App from "./App";
import { AuthProvider } from "./auth/AuthContext";
import "./index.css";

dayjs.locale("tr");

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ConfigProvider locale={trTR}>
      {/* AntApp: message/notification hook'larının (App.useApp) context sağlayıcısı */}
      <AntApp>
        <BrowserRouter>
          <AuthProvider>
            <App />
          </AuthProvider>
        </BrowserRouter>
      </AntApp>
    </ConfigProvider>
  </StrictMode>,
);
