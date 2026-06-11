"""
app/models/enums.py

Tüm enum tanımları tek yerde.
models/ ve schemas/ buradan import eder — döngüsel import riski sıfır.

Değişiklik özeti (v3):
  - OrderStatus: 'edit_granted' YOK + 'mismatch_review' KALDIRILDI.
                 Final fatura tutarı uyuşmazlığı tamamen frontend'de bir uyarı
                 ekranıyla çözülür (doğru faturayı yükle / bu fatura ile devam et).
                 Backend'de özel status, manager onayı veya bildirim YOK.
  - AuditAction: 'order_approved' KALDIRILDI (order_buyed kullanılıyor)
  - AuditAction: 'final_invoice_edited' EKLENDİ — accountant faturayı yeniden
                 yüklediğinde sadece audit log'a yazılır (kimseye bildirim gitmez)
  - NotifType:   'edit_granted', 'mismatch_review', 'final_invoice_edited' YOK
                 (fatura uyuşmazlığı manager'a gitmez)
  - ExtraMetalStatus / ProductionEventType: models.py'den buraya taşındı
"""
import enum


class UserRole(str, enum.Enum):
    manager    = "manager"
    accountant = "accountant"
    staff      = "staff"
    buyer      = "buyer"


class OrderStatus(str, enum.Enum):
    pending_approval = "pending_approval"   # Staff oluşturdu, buyer satın almasını bekliyor
    active           = "active"             # Buyer satın aldı, üretim sürüyor
    on_hold          = "on_hold"            # Beklemede (accountant veya manager)
    cancelled        = "cancelled"          # İptal edildi
    completed        = "completed"          # Tamamlandı, final fatura yüklendi
    deleted          = "deleted"            # Soft-delete


class InvoiceType(str, enum.Enum):
    initial = "initial"   # Sipariş şeması — staff yükler, create-order akışında
    final   = "final"     # İş sonrası tahsilat faturası — accountant yükler


class ExtraMetalStatus(str, enum.Enum):
    pending_approval = "pending_approval"   # Staff oluşturdu, buyer onayını bekliyor
    approved         = "approved"           # Buyer onayladı, Satın Alınacaklar listesinde
    purchased        = "purchased"          # Satın alma tamamlandı, arşivlendi
    rejected         = "rejected"           # Reddedildi


class ProductionEventType(str, enum.Enum):
    metal_arrived         = "metal_arrived"
    cutting_started       = "cutting_started"
    cutting_stopped       = "cutting_stopped"          # note zorunlu: sebep
    cutting_started_again = "cutting_started_again"
    cutting_done          = "cutting_done"
    ready_count_updated   = "ready_count_updated"      # ready_count alanı ile birlikte gelir
    order_completed       = "order_completed"


class NotifCategory(str, enum.Enum):
    """
    Bildirim üst kategorisi — frontend bildirimleri bu alana göre gruplar/sıralar.
    DB'de AYRI bir kolon DEĞİLDİR; her NotifType tek bir kategoriye düşer ve
    kategori `NotifType.category` üzerinden türetilir (NOTIF_TYPE_CATEGORY).

      order    → sipariş yaşam döngüsü + ekstra metal + üretim (tüm iş akışı)
      user     → kullanıcı yönetimi (oluştur/güncelle/rol/aktiflik)
      settings → kişisel ayar & şifre değişiklikleri
    """
    order    = "order"
    user     = "user"
    settings = "settings"


class NotifType(str, enum.Enum):
    # ── İş akışı (mevcut — rol bazlı, korunur) ────────────────────
    new_order             = "new_order"             # Staff yeni sipariş oluşturdu → buyer
    extra_metal_requested = "extra_metal_requested" # Staff extra metal talebi açtı → buyer
    order_buyed           = "order_buyed"           # Buyer satın aldı → accountant (üretimi başlat)
    rejected              = "rejected"              # Manager/buyer reddetti → accountant veya staff
    order_completed       = "order_completed"       # Accountant tamamladı → manager
    production_updated    = "production_updated"    # Accountant üretim güncelledi → manager
    approved              = "approved"              # (eski) buyer onayı; order_buyed ile aynı amaç

    # ── Aktör + müdür bildirimleri (yeni) ─────────────────────────
    # Her mutasyon endpoint'inde işlemi yapan kullanıcıya (aktör) onay ve
    # tüm aktif müdürlere "kim ne yaptı" bilgilendirmesi için kullanılır.
    order_created         = "order_created"         # Sipariş oluşturuldu
    order_updated         = "order_updated"         # Sipariş üst bilgileri güncellendi
    order_deleted         = "order_deleted"         # Sipariş silindi (soft-delete)
    extra_metal_approved  = "extra_metal_approved"  # Ekstra metal talebi onaylandı/satın alındı
    extra_metal_rejected  = "extra_metal_rejected"  # Ekstra metal talebi reddedildi

    user_created          = "user_created"          # Kullanıcı oluşturuldu/canlandırıldı
    user_updated          = "user_updated"          # Kullanıcı bilgileri güncellendi
    user_role_changed     = "user_role_changed"     # Kullanıcı rolü değiştirildi
    user_deactivated      = "user_deactivated"      # Kullanıcı inaktif edildi
    user_reactivated      = "user_reactivated"      # Kullanıcı yeniden aktif edildi

    password_changed      = "password_changed"      # Şifre değiştirildi (self veya müdür)
    settings_changed      = "settings_changed"      # Kişisel ayarlar güncellendi

    @property
    def category(self) -> "NotifCategory":
        """Bu bildirim tipinin ait olduğu üst kategori."""
        return NOTIF_TYPE_CATEGORY.get(self, NotifCategory.order)


# NotifType → NotifCategory eşlemesi (tek doğruluk kaynağı)
NOTIF_TYPE_CATEGORY: dict[NotifType, NotifCategory] = {
    # order
    NotifType.new_order:             NotifCategory.order,
    NotifType.extra_metal_requested: NotifCategory.order,
    NotifType.order_buyed:           NotifCategory.order,
    NotifType.rejected:              NotifCategory.order,
    NotifType.order_completed:       NotifCategory.order,
    NotifType.production_updated:    NotifCategory.order,
    NotifType.approved:              NotifCategory.order,
    NotifType.order_created:         NotifCategory.order,
    NotifType.order_updated:         NotifCategory.order,
    NotifType.order_deleted:         NotifCategory.order,
    NotifType.extra_metal_approved:  NotifCategory.order,
    NotifType.extra_metal_rejected:  NotifCategory.order,
    # user
    NotifType.user_created:          NotifCategory.user,
    NotifType.user_updated:          NotifCategory.user,
    NotifType.user_role_changed:     NotifCategory.user,
    NotifType.user_deactivated:      NotifCategory.user,
    NotifType.user_reactivated:      NotifCategory.user,
    # settings
    NotifType.password_changed:      NotifCategory.settings,
    NotifType.settings_changed:      NotifCategory.settings,
}


def types_in_category(category: NotifCategory) -> list[NotifType]:
    """Bir kategoriye ait tüm NotifType değerleri (endpoint filtresi için)."""
    return [t for t, c in NOTIF_TYPE_CATEGORY.items() if c == category]


class AuditAction(str, enum.Enum):
    # ── Sipariş yaşam döngüsü ─────────────────────────────────────
    order_created   = "order_created"
    order_updated   = "order_updated"
    order_buyed     = "order_buyed"     # Buyer satın aldı → status: active
    order_rejected  = "order_rejected"
    order_cancelled = "order_cancelled"
    order_completed = "order_completed"
    order_deleted   = "order_deleted"

    # ── Fatura ────────────────────────────────────────────────────
    invoice_uploaded      = "invoice_uploaded"
    amount_changed        = "amount_changed"
    ocr_data_edited       = "ocr_data_edited"
    final_invoice_edited  = "final_invoice_edited"  # Accountant mismatch sonrası doğru fatura yükledi

    # ── Durum ─────────────────────────────────────────────────────
    status_changed = "status_changed"

    # ── Metal ─────────────────────────────────────────────────────
    metal_request_added   = "metal_request_added"
    extra_metal_requested = "extra_metal_requested"   # Staff talep açtı
    extra_metal_approved  = "extra_metal_approved"    # Buyer onayladı

    # ── Üretim ────────────────────────────────────────────────────
    production_step_updated = "production_step_updated"
    production_note_added   = "production_note_added"

    # ── Kullanıcı ─────────────────────────────────────────────────
    user_created     = "user_created"
    user_updated     = "user_updated"
    user_role_changed = "user_role_changed"
    user_deactivated = "user_deactivated"
    user_reactivated = "user_reactivated"

    @classmethod
    def user_actions(cls) -> list["AuditAction"]:
        return [a for a in cls if a.value.startswith("user_")]

    @classmethod
    def order_actions(cls) -> list["AuditAction"]:
        return [a for a in cls if not a.value.startswith("user_")]
