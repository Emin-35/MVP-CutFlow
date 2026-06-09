# API Endpoint Referansı (v2)

## Yetki Gösterimi
- `[M]` manager
- `[A]` accountant  
- `[S]` staff
- `[B]` buyer
- `[M+A]` manager veya accountant
- `[M+B]` manager veya buyer
- `[M+S]` manager veya staff

---

## Auth
| Method | Path | Yetki | Açıklama |
|--------|------|-------|----------|
| POST | `/auth/login` | — | Token al |
| POST | `/auth/change-password` | Tüm roller | Kendi şifresini değiştir |

---

## Users (Manager)
| Method | Path | Yetki | Açıklama |
|--------|------|-------|----------|
| GET | `/users/` | `[M]` | Tüm kullanıcıları listele |
| POST | `/users/` | `[M]` | Yeni kullanıcı oluştur |
| GET | `/users/{id}` | `[M]` | Kullanıcı detayı |
| PATCH | `/users/{id}` | `[M]` | Kullanıcı bilgisi güncelle |
| PATCH | `/users/{id}/role` | `[M]` | Rol değiştir |
| PATCH | `/users/{id}/deactivate` | `[M]` | Deaktif et |
| PATCH | `/users/{id}/reactivate` | `[M]` | Aktif et |
| PATCH | `/users/{id}/reset-password` | `[M]` | Şifre sıfırla |

---

## Orders — Staff (sipariş oluşturma)
| Method | Path | Yetki | Açıklama |
|--------|------|-------|----------|
| POST | `/orders/upload-invoice-ocr` | `[S]` | Şema yükle, OCR çalıştır, token al |
| POST | `/orders/create-order` | `[S]` | Sipariş oluştur (token + form verisi) |

---

## Orders — Buyer (satın alma)
| Method | Path | Yetki | Açıklama |
|--------|------|-------|----------|
| GET | `/orders/pending` | `[M+B]` | Onay bekleyen siparişleri listele |
| GET | `/orders/{id}` | `[M+B]` | Sipariş detayı (pending veya active) |
| POST | `/orders/{id}/buy` | `[B]` | Siparişi satın al → status: active |
| GET | `/orders/{id}/extra-metal` | `[M+B]` | Extra metal taleplerini listele |
| POST | `/orders/{id}/extra-metal/{req_id}/approve` | `[B]` | Extra metal talebini onayla/reddet |

---

## Orders — Staff (extra metal talebi)
| Method | Path | Yetki | Açıklama |
|--------|------|-------|----------|
| POST | `/orders/{id}/extra-metal` | `[S]` | Extra metal talebi aç |

---

## Orders — Accountant (üretim yönetimi)
| Method | Path | Yetki | Açıklama |
|--------|------|-------|----------|
| GET | `/orders/active` | `[M+A]` | Aktif siparişleri listele |
| GET | `/orders/{id}` | `[M+A]` | Sipariş detayı (active) |
| PATCH | `/orders/{id}` | `[A]` | Sipariş notu / ready_count güncelle |
| POST | `/orders/{id}/production-event` | `[A]` | Üretim olayı ekle (metal_arrived, cutting_stopped vb.) |
| GET | `/orders/{id}/production-events` | `[M+A]` | Üretim olaylarını listele |
| POST | `/orders/{id}/upload-final-invoice-ocr` | `[A]` | Final fatura yükle, OCR çalıştır |
| POST | `/orders/{id}/submit-final-invoice` | `[A]` | Final faturayı onayla, tamamla → mismatch_review veya completed |
| POST | `/orders/{id}/upload-edit-invoice-ocr` | `[A]` | (edit_granted) Yeni fatura yükle, OCR çalıştır |
| POST | `/orders/{id}/submit-edit-invoice` | `[A]` | (edit_granted) Yeni faturayı gönder → mismatch_review |

---

## Orders — Manager (mismatch çözme + genel)
| Method | Path | Yetki | Açıklama |
|--------|------|-------|----------|
| GET | `/orders/` | `[M]` | Tüm siparişleri listele (filtreli) |
| GET | `/orders/mismatch` | `[M]` | Uyuşmazlık bekleyen siparişler |
| POST | `/orders/{id}/resolve-mismatch` | `[M]` | Uyuşmazlığı çöz (onayla / edit_grant / iptal) |
| POST | `/orders/{id}/cancel` | `[M]` | Siparişi iptal et |

---

## Notifications
| Method | Path | Yetki | Açıklama |
|--------|------|-------|----------|
| GET | `/notifications/` | Tüm roller | Kendi bildirimlerini listele |
| PATCH | `/notifications/read` | Tüm roller | Okundu işaretle |

---

## History & Logs
| Method | Path | Yetki | Açıklama |
|--------|------|-------|----------|
| GET | `/history/orders/{id}` | `[M]` | Sipariş durum geçmişi |
| GET | `/history/audit` | `[M]` | Audit log (filtreli) |

---

## Status Akışı

```
[S] Sipariş oluşturur
         ↓
  pending_approval
         ↓ [B] buy
       active
         ↓ [A] submit-final-invoice (tutar uyuşuyor)
      completed

       active
         ↓ [A] submit-final-invoice (tutar uyuşmuyor)
   do not give access to upload incorrect file
      ┌───────┴
correct file=T
    ↓           
completed

+ cancelled (herhangi bir aşamada [M] iptal edebilir)
+ on_hold   (herhangi bir aşamada [M] bekletebilir)
+ deleted   (soft-delete [M])
```

---

## Notification Tetikleyicileri

| Olay | Tetikleyen | Alıcı | Tip |
|------|-----------|-------|-----|
| Sipariş oluşturuldu | staff | **buyer** | `new_order` |
| Extra metal talebi | staff | **buyer** | `extra_metal_requested` |
| Sipariş satın alındı | buyer | **manager** | `order_buyed` |
| Üretim olayı eklendi | accountant | **manager** | `production_updated` |
| Sipariş tamamlandı | accountant | **manager** | `order_completed` |
| Edit izni verildi | accountant | **manager** | `edit_order` |
| Sipariş reddedildi | buyer/manager | **staff** | `rejected` |
