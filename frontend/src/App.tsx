import { Navigate, Route, Routes } from "react-router-dom";
import RequireAuth from "./auth/RequireAuth";
import AppLayout from "./layouts/AppLayout";
import LoginPage from "./pages/LoginPage";
import OrdersPage from "./pages/OrdersPage";
import OrderDetailPage from "./pages/OrderDetailPage";
import EditOrderPage from "./pages/EditOrderPage";
import NotificationsPage from "./pages/NotificationsPage";
import NewOrderPage from "./pages/NewOrderPage";
import PurchaseListPage from "./pages/PurchaseListPage";
import FinalInvoicePage from "./pages/FinalInvoicePage";
import ProfilePage from "./pages/ProfilePage";
import ComingSoonPage from "./pages/ComingSoonPage";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />

      <Route element={<RequireAuth />}>
        <Route element={<AppLayout />}>
          <Route path="/" element={<Navigate to="/orders" replace />} />
          <Route path="/orders" element={<OrdersPage />} />
          {/* /orders/new, /orders/:orderId'den ÖNCE gelmeli */}
          <Route path="/orders/new" element={<NewOrderPage />} />
          <Route path="/orders/:orderId" element={<OrderDetailPage />} />
          <Route path="/orders/:orderId/edit" element={<EditOrderPage />} />
          <Route path="/purchase-list" element={<PurchaseListPage />} />
          <Route path="/final-invoice" element={<FinalInvoicePage />} />
          <Route path="/notifications" element={<NotificationsPage />} />
          <Route path="/profile" element={<ProfilePage />} />
          {/* Eski /settings bağlantıları profile gitsin */}
          <Route path="/settings" element={<Navigate to="/profile" replace />} />
          <Route path="/users" element={<ComingSoonPage title="Kullanıcı Yönetimi" />} />
          <Route path="/history" element={<ComingSoonPage title="Geçmiş & Denetim" />} />
        </Route>
      </Route>

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
