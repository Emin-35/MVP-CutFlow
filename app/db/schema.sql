-- ============================================================
-- METAL ORDER SYSTEM — Full Database Schema
-- ============================================================

-- ─────────────────────────────────────────
-- ENUM TYPES
-- ─────────────────────────────────────────

CREATE TYPE user_role AS ENUM (
    'manager',
    'accounting'
);

CREATE TYPE order_status AS ENUM (
    'pending_approval',   -- Muhasebe yükledi, müdür onayı bekliyor
    'active',             -- Onaylandı, üretim sürüyor
    'on_hold',            -- Beklemede
    'cancelled',          -- İptal edildi
    'mismatch_review',    -- Tutar uyuşmazlığı, müdür incelemesi bekliyor
    'completed',          -- Teslim edildi, son fatura girildi
    'deleted'             -- Soft-delete (veri kaybı olmasın diye)
);

CREATE TYPE invoice_type AS ENUM (
    'initial',   -- Sipariş faturası (create-order akışında yüklenir)
    'final'      -- İş sonrası tahsilat faturası
);

CREATE TYPE notif_type AS ENUM (
    'approval_needed',    -- Müdüre: yeni sipariş / tutar uyuşmazlığı onay bekliyor
    'approved',           -- Muhasebeye: sipariş onaylandı
    'rejected',           -- Muhasebeye: sipariş reddedildi
    'edit_requested'      -- Muhasebeye: düzenleme istendi
);

CREATE TYPE audit_action AS ENUM (
    'order_created',
    'order_updated',
    'order_approved',
    'order_rejected',
    'order_cancelled',
    'order_completed',
    'order_deleted',

    'invoice_uploaded',
    'amount_changed',
    'ocr_data_edited',
    'status_changed',
    'metal_request_added',
    'production_step_updated',

    'user_created',
    'user_updated',
    'user_deactivated',
    'user_reactivated'
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

    -- Sipariş custom adı (kullanıcının verdiği isim, listede gösterilir)
    order_title     VARCHAR(255) NOT NULL,

    -- Müşteri bilgileri (hepsi opsiyonel)
    customer_name       VARCHAR(255),
    customer_contact    VARCHAR(255),   -- Eski alan, geriye dönük uyumluluk
    customer_phone      VARCHAR(50),    -- Hazır alan, ileride aktif edilebilir
    customer_address    TEXT,           -- Hazır alan, ileride aktif edilebilir

    -- Durum
    status          order_status NOT NULL DEFAULT 'pending_approval',
    rejection_reason TEXT,

    -- Üretim adımları (müdür dashboard)
    metal_arrived       BOOLEAN DEFAULT false,
    cutting_started     BOOLEAN DEFAULT false,
    cutting_done        BOOLEAN DEFAULT false,
    ready_count         INTEGER DEFAULT 0,
    total_count         INTEGER,        -- metal_requests[].quantity toplamından otomatik hesaplanır

    -- Tutarlar
    estimated_amount    NUMERIC(12,2),
    final_amount        NUMERIC(12,2),

    -- Sahiplik & onay
    created_by          INTEGER REFERENCES users(id),
    approved_by         INTEGER REFERENCES users(id),

    -- Tarihler
    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now(),
    approved_at         TIMESTAMPTZ,
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
-- create-order çağrısından önce yüklenen fatura burada bekler.
-- Sipariş oluşunca Invoice tablosuna taşınır, bu kayıt silinir.
-- expires_at geçen kayıtlar periyodik temizlik job'ı ile temizlenir.

CREATE TABLE temp_invoice_files (
    id              SERIAL PRIMARY KEY,
    token           VARCHAR(64) UNIQUE NOT NULL,

    file_path       VARCHAR NOT NULL,
    file_type       VARCHAR NOT NULL,
    original_name   VARCHAR,
    ocr_raw         JSONB,              -- OCR ham çıktı burada bekler

    uploaded_by     INTEGER REFERENCES users(id),
    uploaded_at     TIMESTAMPTZ DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL  -- Varsayılan: +2 saat
);

CREATE INDEX idx_temp_invoice_token   ON temp_invoice_files(token);
CREATE INDEX idx_temp_invoice_expires ON temp_invoice_files(expires_at);


-- ─────────────────────────────────────────
-- ORDER FILES (siparişe eklenen belgeler)
-- ─────────────────────────────────────────

CREATE TABLE order_files (
    id              SERIAL PRIMARY KEY,
    order_id        INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,

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
    order_id        INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    type            invoice_type NOT NULL,

    -- OCR ayrımı — ham veri asla değiştirilmez
    ocr_raw         JSONB,          -- OCR'ın ilk gördüğü ham çıktı
    edited_data     JSONB,          -- Kullanıcının onaylayıp kaydettiği

    file_path       VARCHAR,
    file_type       VARCHAR,
    original_name   VARCHAR,

    amount          NUMERIC(12,2),  -- Onaylanan tutar

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
-- Bir siparişe sınırsız sayıda metal kalemi eklenebilir.
--
-- Frontend preset'leri (her alan editlenebilir):
--   Preset A: width=1500, length=3000, thickness=3, material=GLV, quantity=1
--   Preset B: width=1250, length=2500, thickness=3, material=GLV, quantity=1

CREATE TABLE metal_requests (
    id          SERIAL PRIMARY KEY,
    order_id    INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,

    -- Boyutlar
    width       NUMERIC(8,2)  NOT NULL,     -- En  (mm)
    length      NUMERIC(8,2)  NOT NULL,     -- Boy (mm)
    thickness   NUMERIC(6,3)  NOT NULL,     -- Kalınlık (mm)

    -- Malzeme
    material    VARCHAR(100)  NOT NULL,     -- Örn: GLV

    -- Miktar & hesaplamalar
    quantity    INTEGER       NOT NULL DEFAULT 1,  -- Plaka adedi
    kg          NUMERIC(10,3),                     -- Ağırlık (kg)
    total       NUMERIC(12,2),                     -- Satır toplamı (tutar)

    notes       TEXT,

    created_by  INTEGER REFERENCES users(id),
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_metal_requests_order ON metal_requests(order_id);


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
    order_id    INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,

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