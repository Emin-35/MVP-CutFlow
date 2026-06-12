-- ============================================================
-- METAL ORDER SYSTEM — Full Database Schema (v3)
-- ============================================================
-- v3 Değişiklik özeti (models.py ile senkron):
--   - order_status: 'edit_granted' YOK + 'mismatch_review' KALDIRILDI.
--                   Final fatura tutar uyuşmazlığı tamamen FRONTEND'de bir uyarı
--                   ekranıyla çözülür ("doğru faturayı yükle" / "bu fatura ile
--                   devam et"). Backend'de özel status, manager onayı/bildirimi YOK.
--   - audit_action: 'order_approved' KALDIRILDI (order_buyed kullanılıyor)
--   - audit_action: 'final_invoice_edited' KALIR — accountant faturayı yeniden
--                   yüklediğinde yalnızca audit log'a yazılır (bildirim göndermez)
--   - notif_type:   'edit_granted', 'mismatch_review', 'final_invoice_edited' YOK
--                   (fatura uyuşmazlığı manager'a gitmez)
--   - Dosya yolu standardı: uploads/<rol>/<yıl-ay>/<uuid>.<uzantı>
--                   (file_path bu göreli yolu saklar; bkz app/utils/storage.py)
--
-- v2 Değişiklik özeti:
--   - user_role: 'accounting' → 'accountant' düzeltildi
--   - order_status: 'pending_approval' → buyer onaylıyor ('active')
--   - orders: metal_arrived/cutting_started/cutting_done kolonları kaldırıldı
--             (yerine production_events tablosu eklendi)
--   - orders: note kolonu eklendi (accountant üretim notu)
--   - production_events tablosu eklendi
--   - extra_metal_requests tablosu eklendi
-- ============================================================

CREATE SEQUENCE order_number_seq START WITH 1 CYCLE;

-- ─────────────────────────────────────────
-- ENUM TYPES
-- ─────────────────────────────────────────

CREATE TYPE user_role AS ENUM (
    'manager',    -- Tüm siparişleri görebilir, dashboard, log, bildirim
    'accountant', -- Aktif siparişleri yönetir, final fatura yükler
    'staff',      -- Sipariş oluşturur, extra metal talebi açar
    'buyer'       -- Satın alma yapar, siparişi aktif eder
);

CREATE TYPE order_status AS ENUM (
    'pending_approval',   -- Staff oluşturdu, buyer satın almasını bekliyor
    'active',             -- Buyer satın aldı, üretim sürüyor
    'on_hold',            -- Beklemede
    'cancelled',          -- İptal edildi
    'completed',          -- Tamamlandı, final fatura girildi
    'deleted'             -- Soft-delete
);

CREATE TYPE extra_metal_status AS ENUM (
    'pending_approval',   -- Staff oluşturdu, onay bekliyor
    'approved',           -- Buyer onayladı, "Satın Alınacaklar" listesinde bekliyor
    'purchased',          -- Satın alma tamamlandı, arşivlendi
    'rejected'            -- Reddedildi
);

CREATE TYPE invoice_type AS ENUM (
    'initial',  -- Sipariş şeması (staff yükler, create-order akışında)
    'final'     -- İş sonrası tahsilat faturası (accountant yükler)
);

CREATE TYPE production_event_type AS ENUM (
    'metal_arrived',
    'cutting_started',
    'cutting_stopped',
    'cutting_started_again',
    'cutting_done',
    'ready_count_updated'
    -- NOT: 'order_completed' KALDIRILDI — sipariş tamamlama yalnızca final fatura
    --      akışıyla yapılır. Mevcut DB'lerde eski değer kullanılmayan orphan olarak
    --      kalır (PostgreSQL enum değeri güvenli şekilde DROP edilemez).
);

CREATE TYPE notif_type AS ENUM (
    -- ── İş akışı (rol bazlı, korunur) ──
    'new_order',               -- Staff yeni sipariş oluşturdu → buyer
    'extra_metal_requested',   -- Staff extra metal talebi açtı → buyer
    'order_buyed',             -- Buyer satın aldı → accountant (üretimi başlat)
    'rejected',                -- Manager/buyer reddetti → accountant/staff
    'order_completed',         -- Accountant tamamladı → manager
    'production_updated',      -- Accountant üretim güncelledi → manager
    'approved',                -- (eski) buyer onayı; order_buyed ile aynı amaç

    -- ── Aktör + müdür bildirimleri (kategori: order) ──
    'order_created',           -- Sipariş oluşturuldu
    'order_updated',           -- Sipariş üst bilgileri güncellendi
    'order_deleted',           -- Sipariş silindi (soft-delete)
    'order_revision_requested',-- Buyer staff'tan düzenleme istedi
    'extra_metal_approved',    -- Ekstra metal onaylandı/satın alındı
    'extra_metal_rejected',    -- Ekstra metal reddedildi

    -- ── Kullanıcı yönetimi (kategori: user) ──
    'user_created',            -- Kullanıcı oluşturuldu/canlandırıldı
    'user_updated',            -- Kullanıcı bilgileri güncellendi
    'user_role_changed',       -- Kullanıcı rolü değiştirildi
    'user_deactivated',        -- Kullanıcı inaktif edildi
    'user_reactivated',        -- Kullanıcı yeniden aktif edildi

    -- ── Kişisel ayar/şifre (kategori: settings) ──
    'password_changed',        -- Şifre değiştirildi (self veya müdür)
    'settings_changed'         -- Kişisel ayarlar güncellendi
);

-- NOT: Bildirim KATEGORİSİ (order/user/settings) DB'de ayrı kolon değildir;
--      her notif_type tek bir kategoriye düşer ve kategori uygulama katmanında
--      türetilir (app/models/enums.py → NOTIF_TYPE_CATEGORY).

CREATE TYPE audit_action AS ENUM (
    -- Sipariş yaşam döngüsü
    'order_created',
    'order_updated',
    'order_buyed',             -- Buyer satın aldı, status → active
    'order_rejected',
    'order_cancelled',
    'order_completed',
    'order_deleted',

    -- Fatura
    'invoice_uploaded',
    'amount_changed',
    'ocr_data_edited',
    'final_invoice_edited',    -- Accountant mismatch sonrası doğru faturayı yükledi

    -- Durum
    'status_changed',

    -- Metal
    'metal_request_added',
    'extra_metal_requested',   -- Staff extra metal talebi açtı
    'extra_metal_approved',    -- Buyer extra metal onayladı

    -- Üretim
    'production_step_updated', -- Genel üretim adımı (production_events)
    'production_note_added',   -- Accountant not ekledi

    -- Kullanıcı
    'user_created',
    'user_updated',
    'user_role_changed',
    'user_deactivated',
    'user_reactivated',

    -- Güvenlik
    'unauthorized_file_access' -- Yetkisiz dosya erişim denemesi (IDOR koruması)
);


-- ─────────────────────────────────────────
-- USERS
-- ─────────────────────────────────────────

CREATE TABLE users (
    id              SERIAL PRIMARY KEY,
    username        VARCHAR(100) UNIQUE NOT NULL,
    email           VARCHAR(255) UNIQUE NOT NULL,
    password_hash   VARCHAR(255) NOT NULL,
    role            user_role NOT NULL,
    first_name      VARCHAR(100),               -- opsiyonel kişisel bilgi
    last_name       VARCHAR(100),               -- opsiyonel kişisel bilgi
    phone           VARCHAR(30),                -- opsiyonel kişisel bilgi
    is_active       BOOLEAN DEFAULT true,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);


-- ─────────────────────────────────────────
-- ORDERS
-- ─────────────────────────────────────────

CREATE TABLE orders (
    id              SERIAL PRIMARY KEY,
    order_number    VARCHAR(50) UNIQUE NOT NULL,  -- ORD-2024-001
    order_title     VARCHAR(255) NOT NULL,

    -- Müşteri bilgileri
    customer_name       VARCHAR(255),
    customer_contact    VARCHAR(255),   -- Şimdilik boş bırakılacak
    customer_phone      VARCHAR(50),    -- Şimdilik boş bırakılacak
    customer_address    TEXT,           -- Şimdilik boş bırakılacak

    -- Durum
    status              order_status NOT NULL DEFAULT 'pending_approval',
    rejection_reason    TEXT,
    buyer_note          TEXT,           -- Buyer'ın düzenleme isterken düştüğü not

    -- Üretim takibi
    -- NOT: metal_arrived, cutting_started, cutting_done production_events tablosuna taşındı.
    --      Sadece anlık sayısal değerler burada tutuluyor.
    ready_count         INTEGER DEFAULT 0,       -- Şu an hazır / gönderilebilir ürün sayısı
    total_count         INTEGER,                 -- metal_requests[].quantity toplamı
    note                TEXT,                    -- Accountant üretim notu (makine arızası vb.)

    -- Tutarlar
    estimated_amount    NUMERIC(12,2),
    final_amount        NUMERIC(12,2),

    -- Sahiplik
    created_by          INTEGER REFERENCES users(id),   -- staff
    bought_by           INTEGER REFERENCES users(id),   -- buyer
    completed_by        INTEGER REFERENCES users(id),   -- accountant

    -- Tarihler
    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now(),
    bought_at           TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ
);

CREATE INDEX idx_orders_status      ON orders(status);
CREATE INDEX idx_orders_created_at  ON orders(created_at DESC);
CREATE INDEX idx_orders_title       ON orders(order_title);
CREATE INDEX idx_orders_customer    ON orders(customer_name);
CREATE INDEX idx_orders_estimated   ON orders(estimated_amount);


-- ─────────────────────────────────────────
-- TEMP INVOICE FILES (sipariş öncesi geçici depo)
-- ─────────────────────────────────────────

CREATE TABLE temp_invoice_files (
    id              SERIAL PRIMARY KEY,
    token           VARCHAR(64) UNIQUE NOT NULL,
    file_path       VARCHAR NOT NULL,    -- uploads/<rol>/<yıl-ay>/<uuid>.<uzantı>
    file_type       VARCHAR NOT NULL,
    original_name   VARCHAR,             -- yalnızca metadata; path'e asla girmez
    ocr_raw         JSONB,
    uploaded_by     INTEGER REFERENCES users(id),
    uploaded_at     TIMESTAMPTZ DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_temp_invoice_token   ON temp_invoice_files(token);
CREATE INDEX idx_temp_invoice_expires ON temp_invoice_files(expires_at);


-- ─────────────────────────────────────────
-- ORDER FILES
-- ─────────────────────────────────────────

CREATE TABLE order_files (
    id              SERIAL PRIMARY KEY,
    order_id        INTEGER NOT NULL REFERENCES orders(id), -- ON DELETE CASCADE KALDIRILDI
    file_path       VARCHAR NOT NULL,
    file_type       VARCHAR NOT NULL,
    original_name   VARCHAR,
    uploaded_by     INTEGER REFERENCES users(id),
    uploaded_at     TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_order_files_order ON order_files(order_id);


-- ─────────────────────────────────────────
-- INVOICES
-- ─────────────────────────────────────────

CREATE TABLE invoices (
    id              SERIAL PRIMARY KEY,
    order_id        INTEGER NOT NULL REFERENCES orders(id), -- ON DELETE CASCADE KALDIRILDI
    type            invoice_type NOT NULL,
    ocr_raw         JSONB,
    edited_data     JSONB,
    file_path       VARCHAR,
    file_type       VARCHAR,
    original_name   VARCHAR,
    amount          NUMERIC(12,2),
    uploaded_by     INTEGER REFERENCES users(id),
    uploaded_at     TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_invoice_order    ON invoices(order_id);
CREATE INDEX idx_invoice_type     ON invoices(order_id, type);
CREATE INDEX idx_invoice_amount   ON invoices(amount);
CREATE INDEX idx_invoice_uploaded ON invoices(uploaded_at DESC);


-- ─────────────────────────────────────────
-- METAL REQUESTS
-- ─────────────────────────────────────────

CREATE TABLE metal_requests (
    id          SERIAL PRIMARY KEY,
    order_id        INTEGER NOT NULL REFERENCES orders(id), -- ON DELETE CASCADE KALDIRILDI
    width       NUMERIC(8,2)  NOT NULL,
    length      NUMERIC(8,2)  NOT NULL,
    thickness   NUMERIC(6,3)  NOT NULL,
    material    VARCHAR(100)  NOT NULL,
    quantity    INTEGER       NOT NULL DEFAULT 1,
    kg          NUMERIC(10,3),
    total       NUMERIC(12,2),
    notes       TEXT,
    created_by  INTEGER REFERENCES users(id),
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_metal_requests_order ON metal_requests(order_id);


-- ─────────────────────────────────────────
-- EXTRA METAL REQUESTS
-- ─────────────────────────────────────────
-- Staff, aktif bir sipariş için ekstra metal talebi açar.
-- Buyer görür, onaylarsa satın alır ve bu kayıt siparişe bağlı loglanır.

CREATE TABLE extra_metal_requests (
    id              SERIAL PRIMARY KEY,
    order_id        INTEGER NOT NULL REFERENCES orders(id), -- ON DELETE CASCADE KALDIRILDI

    -- Talep edilen metal detayları
    width           NUMERIC(8,2)  NOT NULL,
    length          NUMERIC(8,2)  NOT NULL,
    thickness       NUMERIC(6,3)  NOT NULL,
    material        VARCHAR(100)  NOT NULL,
    quantity        INTEGER       NOT NULL DEFAULT 1,
    kg              NUMERIC(10,3),
    total            NUMERIC(12,2),          -- otomatik hesaplanan ağırlık/alan
    estimated_amount NUMERIC(12,2),          -- alınan metalin elle girilen fiyatı

    reason          TEXT,                    -- staff'ın ekstra gerekiyor
    buyer_note      TEXT,                    -- Buyer'ın satın alma notu
    
    status          extra_metal_status NOT NULL DEFAULT 'pending_approval',
    approved_by     INTEGER REFERENCES users(id),   -- buyer
    approved_at     TIMESTAMPTZ,

    created_by      INTEGER REFERENCES users(id),   -- staff
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_extra_metal_order    ON extra_metal_requests(order_id);
CREATE INDEX idx_extra_metal_approved ON extra_metal_requests(status);

-- ─────────────────────────────────────────
-- PRODUCTION EVENTS
-- ─────────────────────────────────────────
-- metal_arrived, cutting_started, cutting_stopped vb. olaylar burada loglanır.
-- Tekrarlanabilir (cutting_stopped birden fazla kez olabilir).
-- Manager dashboard bu tabloyu okur.

CREATE TABLE production_events (
    id          SERIAL PRIMARY KEY,
    order_id        INTEGER NOT NULL REFERENCES orders(id), -- ON DELETE CASCADE KALDIRILDI

    event_type  production_event_type NOT NULL,
    note        TEXT,                    -- cutting_stopped için sebep notu
    ready_count INTEGER,                 -- ready_count_updated için yeni değer

    created_by  INTEGER REFERENCES users(id),   -- accountant
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_production_events_order ON production_events(order_id);
CREATE INDEX idx_production_events_time  ON production_events(created_at DESC);
CREATE INDEX idx_production_events_type  ON production_events(order_id, event_type);


-- ─────────────────────────────────────────
-- NOTIFICATIONS
-- ─────────────────────────────────────────

CREATE TABLE notifications (
    id              SERIAL PRIMARY KEY,
    recipient_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    order_id        INTEGER REFERENCES orders(id) ON DELETE SET NULL,
    type            notif_type NOT NULL,
    message         TEXT,
    is_read         BOOLEAN DEFAULT false,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_notification_user ON notifications(recipient_id);
CREATE INDEX idx_notification_read ON notifications(recipient_id, is_read);


-- ─────────────────────────────────────────
-- ORDER STATUS HISTORY
-- ─────────────────────────────────────────

CREATE TABLE order_status_history (
    id          SERIAL PRIMARY KEY,
    order_id        INTEGER NOT NULL REFERENCES orders(id), -- ON DELETE CASCADE KALDIRILDI
    old_status  order_status,
    new_status  order_status NOT NULL,
    changed_by  INTEGER REFERENCES users(id),
    note        TEXT,
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_status_history_order ON order_status_history(order_id);
CREATE INDEX idx_status_history_time  ON order_status_history(created_at DESC);


-- ─────────────────────────────────────────
-- AUDIT LOGS
-- ─────────────────────────────────────────

CREATE TABLE audit_logs (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER REFERENCES users(id)  ON DELETE SET NULL,
    order_id    INTEGER REFERENCES orders(id) ON DELETE SET NULL,
    action      audit_action NOT NULL,
    old_value   JSONB,
    new_value   JSONB,
    ip_address  VARCHAR(45),
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_audit_order   ON audit_logs(order_id);
CREATE INDEX idx_audit_user    ON audit_logs(user_id);
CREATE INDEX idx_audit_created ON audit_logs(created_at DESC);
CREATE INDEX idx_audit_action  ON audit_logs(action);


-- ─────────────────────────────────────────
-- FILES (merkezi dosya metadata — object storage / R2)
-- ─────────────────────────────────────────
-- Dosyanın kendisi storage'da (yerel disk veya R2) tutulur; burada metadata.
-- storage_key: StorageBackend erişim anahtarı (uploads/<rol>/<yıl-ay>/<uuid>.<ext>)

CREATE TABLE files (
    file_id         UUID PRIMARY KEY,              -- uygulama uuid4 üretir
    file_name       VARCHAR(255),                  -- sanitize edilmiş orijinal ad
    storage_key     VARCHAR(512) UNIQUE NOT NULL,
    content_type    VARCHAR(120),
    file_size       BIGINT,                        -- byte
    kind            VARCHAR(40),                   -- order_schema | invoice_final | extra ...
    order_id        INTEGER REFERENCES orders(id) ON DELETE SET NULL,
    uploaded_by     INTEGER REFERENCES users(id)  ON DELETE SET NULL,
    uploaded_at     TIMESTAMPTZ DEFAULT now(),
    deleted_at      TIMESTAMPTZ,                   -- soft-delete işareti (NULL = canlı)
    retention_until TIMESTAMPTZ                    -- NULL = süresiz sakla
);

CREATE INDEX idx_files_order      ON files(order_id);
CREATE INDEX idx_files_key        ON files(storage_key);
CREATE INDEX idx_files_retention  ON files(retention_until);


-- ============================================================
-- MIGRATION — mevcut (kurulu) veritabanları için
-- ============================================================
-- Yukarıdaki CREATE TYPE blokları yalnızca SIFIRDAN kurulumda çalışır.
-- Halihazırda kurulu bir DB'de notif_type enum'una yeni değerleri eklemek için
-- aşağıdaki idempotent komutları çalıştırın (PostgreSQL 10+; IF NOT EXISTS güvenli).
--   notif_type'a eklenen aktör/müdür + kullanıcı/ayar bildirim tipleri:
ALTER TYPE notif_type ADD VALUE IF NOT EXISTS 'order_created';
ALTER TYPE notif_type ADD VALUE IF NOT EXISTS 'order_updated';
ALTER TYPE notif_type ADD VALUE IF NOT EXISTS 'order_deleted';
ALTER TYPE notif_type ADD VALUE IF NOT EXISTS 'extra_metal_approved';
ALTER TYPE notif_type ADD VALUE IF NOT EXISTS 'extra_metal_rejected';
ALTER TYPE notif_type ADD VALUE IF NOT EXISTS 'user_created';
ALTER TYPE notif_type ADD VALUE IF NOT EXISTS 'user_updated';
ALTER TYPE notif_type ADD VALUE IF NOT EXISTS 'user_role_changed';
ALTER TYPE notif_type ADD VALUE IF NOT EXISTS 'user_deactivated';
ALTER TYPE notif_type ADD VALUE IF NOT EXISTS 'user_reactivated';
ALTER TYPE notif_type ADD VALUE IF NOT EXISTS 'password_changed';
ALTER TYPE notif_type ADD VALUE IF NOT EXISTS 'settings_changed';
-- NOT: ALTER TYPE ... ADD VALUE bazı PostgreSQL sürümlerinde transaction bloğu
--      içinde çalışmaz; bu komutları autocommit modunda tek tek çalıştırın.

-- users tablosuna eklenen opsiyonel kişisel bilgi kolonları:
ALTER TABLE users ADD COLUMN IF NOT EXISTS first_name VARCHAR(100);
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_name  VARCHAR(100);
ALTER TABLE users ADD COLUMN IF NOT EXISTS phone      VARCHAR(30);

-- Buyer revizyon notu + yeni bildirim/audit enum değerleri:
ALTER TABLE orders ADD COLUMN IF NOT EXISTS buyer_note TEXT;
ALTER TYPE notif_type   ADD VALUE IF NOT EXISTS 'order_revision_requested';
ALTER TYPE audit_action ADD VALUE IF NOT EXISTS 'unauthorized_file_access';

-- Ekstra metal: estimated_cost → total (otomatik) + estimated_amount (elle girilen fiyat)
ALTER TABLE extra_metal_requests ADD COLUMN IF NOT EXISTS total NUMERIC(12,2);
ALTER TABLE extra_metal_requests ADD COLUMN IF NOT EXISTS estimated_amount NUMERIC(12,2);
-- Eski estimated_cost değerlerini total'a taşı, sonra kolonu kaldır (yalnızca hâlâ varsa)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name='extra_metal_requests' AND column_name='estimated_cost') THEN
        UPDATE extra_metal_requests SET total = estimated_cost WHERE total IS NULL;
        ALTER TABLE extra_metal_requests DROP COLUMN estimated_cost;
    END IF;
END $$;

-- Merkezi dosya metadata tablosu (object storage / R2 altyapısı)
CREATE TABLE IF NOT EXISTS files (
    file_id         UUID PRIMARY KEY,
    file_name       VARCHAR(255),
    storage_key     VARCHAR(512) UNIQUE NOT NULL,
    content_type    VARCHAR(120),
    file_size       BIGINT,
    kind            VARCHAR(40),
    order_id        INTEGER REFERENCES orders(id) ON DELETE SET NULL,
    uploaded_by     INTEGER REFERENCES users(id)  ON DELETE SET NULL,
    uploaded_at     TIMESTAMPTZ DEFAULT now(),
    deleted_at      TIMESTAMPTZ,
    retention_until TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_files_order     ON files(order_id);
CREATE INDEX IF NOT EXISTS idx_files_key       ON files(storage_key);
CREATE INDEX IF NOT EXISTS idx_files_retention ON files(retention_until);