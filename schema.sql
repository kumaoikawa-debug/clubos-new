PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS clubs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  plan TEXT NOT NULL DEFAULT 'pro',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ai_credit_accounts (
  club_id INTEGER PRIMARY KEY,
  balance INTEGER NOT NULL DEFAULT 0,
  monthly_quota INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY (club_id) REFERENCES clubs(id)
);

CREATE TABLE IF NOT EXISTS ai_credit_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  club_id INTEGER NOT NULL,
  type TEXT NOT NULL,
  amount INTEGER NOT NULL,
  source_type TEXT,
  source_id TEXT,
  note TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (club_id) REFERENCES clubs(id)
);

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  phone TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS club_members (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  club_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  level TEXT NOT NULL DEFAULT '普通会员',
  club_points_balance INTEGER NOT NULL DEFAULT 0,
  current_tier_id INTEGER,
  lifetime_activity_spend REAL NOT NULL DEFAULT 0,
  activity_count INTEGER NOT NULL DEFAULT 0,
  tier_updated_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(club_id, user_id),
  FOREIGN KEY (club_id) REFERENCES clubs(id),
  FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS club_point_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  club_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  type TEXT NOT NULL,
  amount INTEGER NOT NULL,
  source_type TEXT,
  source_id TEXT,
  note TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS gear_point_accounts (
  user_id INTEGER PRIMARY KEY,
  balance INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS gear_point_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  type TEXT NOT NULL,
  amount INTEGER NOT NULL,
  source_type TEXT,
  source_id TEXT,
  note TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS activities (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  club_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft',
  event_date TEXT,
  location TEXT,
  price REAL NOT NULL DEFAULT 0,
  capacity INTEGER NOT NULL DEFAULT 0,
  points_enabled INTEGER NOT NULL DEFAULT 1,
  earn_club_points INTEGER NOT NULL DEFAULT 1,
  accept_club_points INTEGER NOT NULL DEFAULT 1,
  club_points_max_discount_percent REAL NOT NULL DEFAULT 100,
  accept_gear_points INTEGER NOT NULL DEFAULT 1,
  gear_points_max_discount_amount REAL,
  club_points_earn_rate_override REAL,
  refund_enabled INTEGER NOT NULL DEFAULT 1,
  refund_policy_json TEXT,
  participant_form_policy_json TEXT,
  cover TEXT,
  activity_master_json TEXT NOT NULL,
  detail_json TEXT NOT NULL,
  source_json TEXT,
  detail_version_id INTEGER,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (club_id) REFERENCES clubs(id)
);

-- 活动详情的版本历史：每次 AI 生成 / 重新生成都留一份不可变快照，
-- detail_version_id 指向当前生效的那一版，因此「恢复上一版」只是换指针，不调用 AI、不扣 Credits。
CREATE TABLE IF NOT EXISTS activity_detail_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  club_id INTEGER NOT NULL,
  activity_id INTEGER NOT NULL,
  version_no INTEGER NOT NULL,
  origin TEXT NOT NULL DEFAULT 'ai-generate',
  direction TEXT,
  facts_refreshed INTEGER NOT NULL DEFAULT 0,
  narrative TEXT,
  outline TEXT,
  detail_json TEXT NOT NULL,
  master_json TEXT,
  credits_charged INTEGER NOT NULL DEFAULT 0,
  gateway_mode TEXT,
  gateway_model TEXT,
  status TEXT NOT NULL DEFAULT 'ready',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (activity_id) REFERENCES activities(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_activity_detail_versions_no ON activity_detail_versions(activity_id,version_no);
-- 一场活动同时只允许有一版在生成中：并发再次触发换一版会插入失败而不是各扣一次 AI Credits。
CREATE UNIQUE INDEX IF NOT EXISTS idx_activity_detail_pending ON activity_detail_versions(activity_id) WHERE status='pending';

CREATE TABLE IF NOT EXISTS content_assets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  club_id INTEGER NOT NULL,
  activity_id INTEGER NOT NULL,
  channel TEXT NOT NULL,
  title TEXT,
  body_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (activity_id) REFERENCES activities(id)
);

CREATE TABLE IF NOT EXISTS registrations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  activity_id INTEGER NOT NULL,
  occurrence_id INTEGER,
  club_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'paid',
  original_amount REAL NOT NULL DEFAULT 0,
  club_point_discount REAL NOT NULL DEFAULT 0,
  platform_point_subsidy REAL NOT NULL DEFAULT 0,
  club_benefit_discount REAL NOT NULL DEFAULT 0,
  platform_benefit_subsidy REAL NOT NULL DEFAULT 0,
  benefit_redemption_ids_json TEXT NOT NULL DEFAULT '[]',
  amount REAL NOT NULL DEFAULT 0,
  commerce_order_id TEXT,
  booking_ref TEXT,
  points_policy_snapshot_json TEXT,
  payment_status TEXT NOT NULL DEFAULT 'succeeded',
  refund_status TEXT NOT NULL DEFAULT 'none',
  refund_request_id TEXT,
  refund_cash_amount REAL NOT NULL DEFAULT 0,
  refund_percent REAL,
  retained_cash_amount REAL NOT NULL DEFAULT 0,
  refund_policy_snapshot_json TEXT,
  refunded_at TEXT,
  participant_count INTEGER NOT NULL DEFAULT 1,
  participant_policy_snapshot_json TEXT,
  partial_refund_cash_total REAL NOT NULL DEFAULT 0,
  partial_refund_retained_cash_total REAL NOT NULL DEFAULT 0,
  partial_refund_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (activity_id) REFERENCES activities(id),
  FOREIGN KEY (occurrence_id) REFERENCES activity_occurrences(id),
  FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS registration_participants (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  registration_id INTEGER NOT NULL,
  activity_id INTEGER NOT NULL,
  occurrence_id INTEGER NOT NULL,
  club_id INTEGER NOT NULL,
  payer_user_id INTEGER NOT NULL,
  linked_user_id INTEGER,
  name TEXT NOT NULL,
  phone TEXT,
  relation_to_payer TEXT,
  id_type TEXT,
  id_number TEXT,
  emergency_contact_name TEXT,
  emergency_contact_phone TEXT,
  notes TEXT,
  form_status TEXT NOT NULL DEFAULT 'incomplete',
  insurance_status TEXT NOT NULL DEFAULT 'pending',
  insurance_provider TEXT,
  insurance_policy_no TEXT,
  insured_at TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  refund_status TEXT NOT NULL DEFAULT 'none',
  refund_request_id TEXT,
  refunded_at TEXT,
  replacement_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (registration_id) REFERENCES registrations(id),
  FOREIGN KEY (activity_id) REFERENCES activities(id),
  FOREIGN KEY (occurrence_id) REFERENCES activity_occurrences(id),
  FOREIGN KEY (payer_user_id) REFERENCES users(id),
  FOREIGN KEY (linked_user_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_participants_registration ON registration_participants(registration_id);
CREATE INDEX IF NOT EXISTS idx_participants_occurrence ON registration_participants(occurrence_id,status);
CREATE INDEX IF NOT EXISTS idx_participants_club ON registration_participants(club_id,status);

CREATE TABLE IF NOT EXISTS participant_financial_allocations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  registration_id INTEGER NOT NULL,
  participant_id INTEGER NOT NULL UNIQUE,
  original_amount REAL NOT NULL DEFAULT 0,
  cash_paid REAL NOT NULL DEFAULT 0,
  club_points_used INTEGER NOT NULL DEFAULT 0,
  club_point_discount REAL NOT NULL DEFAULT 0,
  gear_points_used INTEGER NOT NULL DEFAULT 0,
  platform_point_subsidy REAL NOT NULL DEFAULT 0,
  club_benefit_discount REAL NOT NULL DEFAULT 0,
  platform_benefit_subsidy REAL NOT NULL DEFAULT 0,
  club_points_earned INTEGER NOT NULL DEFAULT 0,
  refund_cash_amount REAL NOT NULL DEFAULT 0,
  retained_cash_amount REAL NOT NULL DEFAULT 0,
  refunded_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(registration_id) REFERENCES registrations(id),
  FOREIGN KEY(participant_id) REFERENCES registration_participants(id)
);
CREATE INDEX IF NOT EXISTS idx_participant_alloc_registration ON participant_financial_allocations(registration_id);

CREATE TABLE IF NOT EXISTS participant_change_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  participant_id INTEGER NOT NULL,
  registration_id INTEGER NOT NULL,
  action TEXT NOT NULL,
  before_json TEXT,
  after_json TEXT,
  actor_type TEXT,
  note TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (participant_id) REFERENCES registration_participants(id),
  FOREIGN KEY (registration_id) REFERENCES registrations(id)
);
CREATE INDEX IF NOT EXISTS idx_participant_changes_registration ON participant_change_logs(registration_id,created_at DESC);

CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  sku TEXT NOT NULL UNIQUE,
  price REAL NOT NULL,
  stock INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active',
  image_url TEXT,
  category TEXT,
  commission_rate REAL NOT NULL DEFAULT 0.08,
  average_cost REAL NOT NULL DEFAULT 0,
  last_purchase_cost REAL NOT NULL DEFAULT 0,
  last_inbound_at TEXT,
  reorder_point INTEGER NOT NULL DEFAULT 5,
  commerce_product_id TEXT,
  commerce_variant_id TEXT,
  commerce_inventory_item_id TEXT,
  commerce_sync_status TEXT NOT NULL DEFAULT 'local',
  commerce_sync_error TEXT,
  last_commerce_sync_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS gear_orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  source_club_id INTEGER NOT NULL,
  total REAL NOT NULL,
  cash_paid REAL NOT NULL DEFAULT 0,
  platform_point_subsidy REAL NOT NULL DEFAULT 0,
  platform_benefit_subsidy REAL NOT NULL DEFAULT 0,
  benefit_redemption_ids_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'paid',
  tracking_no TEXT,
  carrier TEXT,
  club_commission REAL NOT NULL DEFAULT 0,
  after_sales_status TEXT,
  refunded_at TEXT,
  refunded_cash_total REAL NOT NULL DEFAULT 0,
  refunded_goods_total REAL NOT NULL DEFAULT 0,
  commerce_order_id TEXT,
  commerce_cart_id TEXT,
  commerce_fulfillment_id TEXT,
  commerce_sync_status TEXT NOT NULL DEFAULT 'local',
  shipped_at TEXT,
  delivered_at TEXT,
  payment_status TEXT NOT NULL DEFAULT 'succeeded',
  refund_status TEXT NOT NULL DEFAULT 'none',
  refund_request_id TEXT,
  shipping_cost REAL NOT NULL DEFAULT 0,
  packaging_cost REAL NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id),
  FOREIGN KEY (source_club_id) REFERENCES clubs(id)
);

CREATE TABLE IF NOT EXISTS gear_order_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id INTEGER NOT NULL,
  product_id INTEGER NOT NULL,
  quantity INTEGER NOT NULL,
  unit_price REAL NOT NULL,
  unit_cost_snapshot REAL NOT NULL DEFAULT 0,
  FOREIGN KEY (order_id) REFERENCES gear_orders(id),
  FOREIGN KEY (product_id) REFERENCES products(id)
);



-- v0.18 Gear after-sales / return / exchange
CREATE TABLE IF NOT EXISTS after_sales_cases (
  id TEXT PRIMARY KEY,
  order_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  source_club_id INTEGER NOT NULL,
  case_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending_review',
  reason TEXT,
  evidence_json TEXT NOT NULL DEFAULT '[]',
  note TEXT,
  decision_note TEXT,
  requested_goods_amount REAL NOT NULL DEFAULT 0,
  requested_refund_amount REAL NOT NULL DEFAULT 0,
  approved_refund_amount REAL NOT NULL DEFAULT 0,
  refund_request_id TEXT,
  reviewed_by TEXT,
  reviewed_at TEXT,
  return_carrier TEXT,
  return_tracking_no TEXT,
  returned_at TEXT,
  received_at TEXT,
  exchange_carrier TEXT,
  exchange_tracking_no TEXT,
  exchange_shipped_at TEXT,
  completed_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(order_id) REFERENCES gear_orders(id),
  FOREIGN KEY(user_id) REFERENCES users(id),
  FOREIGN KEY(source_club_id) REFERENCES clubs(id)
);
CREATE INDEX IF NOT EXISTS idx_after_sales_order ON after_sales_cases(order_id,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_after_sales_user ON after_sales_cases(user_id,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_after_sales_status ON after_sales_cases(status,created_at DESC);

CREATE TABLE IF NOT EXISTS after_sales_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  case_id TEXT NOT NULL,
  order_item_id INTEGER NOT NULL,
  quantity INTEGER NOT NULL,
  requested_amount REAL NOT NULL DEFAULT 0,
  restocked INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(case_id) REFERENCES after_sales_cases(id),
  FOREIGN KEY(order_item_id) REFERENCES gear_order_items(id)
);
CREATE INDEX IF NOT EXISTS idx_after_sales_items_case ON after_sales_items(case_id);

CREATE TABLE IF NOT EXISTS commission_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  club_id INTEGER NOT NULL,
  order_id INTEGER NOT NULL,
  amount REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  ledger_type TEXT NOT NULL DEFAULT 'earn',
  note TEXT,
  frozen_at TEXT,
  available_at TEXT,
  settlement_id TEXT,
  settled_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_commission_ledger_club_status ON commission_ledger(club_id,status,created_at);
CREATE INDEX IF NOT EXISTS idx_commission_ledger_order ON commission_ledger(order_id,ledger_type);

CREATE TABLE IF NOT EXISTS commission_settlements (
  id TEXT PRIMARY KEY,
  club_id INTEGER NOT NULL,
  gross_amount REAL NOT NULL DEFAULT 0,
  deduction_amount REAL NOT NULL DEFAULT 0,
  net_amount REAL NOT NULL DEFAULT 0,
  entry_count INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'paid',
  payment_ref TEXT NOT NULL UNIQUE,
  note TEXT,
  created_by TEXT NOT NULL DEFAULT 'platform',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  paid_at TEXT,
  FOREIGN KEY (club_id) REFERENCES clubs(id)
);
CREATE INDEX IF NOT EXISTS idx_commission_settlements_club ON commission_settlements(club_id,created_at DESC);

CREATE TABLE IF NOT EXISTS platform_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_usage_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  club_id INTEGER NOT NULL,
  task_type TEXT NOT NULL,
  request_id TEXT NOT NULL,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  status TEXT NOT NULL,
  input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  provider_cost REAL NOT NULL DEFAULT 0,
  credits_charged INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (club_id) REFERENCES clubs(id)
);

CREATE INDEX IF NOT EXISTS idx_ai_usage_club_created ON ai_usage_records(club_id, created_at DESC);

CREATE TABLE IF NOT EXISTS activity_occurrences (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  activity_id INTEGER NOT NULL,
  club_id INTEGER NOT NULL,
  start_at TEXT NOT NULL,
  end_at TEXT,
  price REAL NOT NULL DEFAULT 0,
  capacity INTEGER NOT NULL DEFAULT 0,
  sold INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'open',
  label TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(activity_id) REFERENCES activities(id),
  FOREIGN KEY(club_id) REFERENCES clubs(id)
);

CREATE TABLE IF NOT EXISTS point_redemptions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  club_id INTEGER,
  order_kind TEXT NOT NULL,
  order_id INTEGER,
  point_type TEXT NOT NULL,
  points_used INTEGER NOT NULL,
  cash_value REAL NOT NULL DEFAULT 0,
  funding_owner TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);


CREATE TABLE IF NOT EXISTS point_adjustment_debt (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  club_id INTEGER,
  point_type TEXT NOT NULL,
  amount INTEGER NOT NULL,
  source_type TEXT,
  source_id TEXT,
  note TEXT,
  resolved INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS checkout_intents (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  user_id INTEGER NOT NULL,
  club_id INTEGER,
  status TEXT NOT NULL DEFAULT 'pending_payment',
  original_amount REAL NOT NULL DEFAULT 0,
  cash_amount REAL NOT NULL DEFAULT 0,
  club_points_reserved INTEGER NOT NULL DEFAULT 0,
  gear_points_reserved INTEGER NOT NULL DEFAULT 0,
  club_point_discount REAL NOT NULL DEFAULT 0,
  platform_point_subsidy REAL NOT NULL DEFAULT 0,
  club_benefit_discount REAL NOT NULL DEFAULT 0,
  platform_benefit_subsidy REAL NOT NULL DEFAULT 0,
  benefit_redemption_ids_json TEXT NOT NULL DEFAULT '[]',
  commerce_cart_id TEXT,
  commerce_order_id TEXT,
  result_id TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  points_policy_snapshot_json TEXT,
  participant_count INTEGER NOT NULL DEFAULT 1,
  participant_policy_snapshot_json TEXT,
  payment_status TEXT NOT NULL DEFAULT 'pending',
  refund_status TEXT NOT NULL DEFAULT 'none',
  paid_at TEXT,
  cancelled_at TEXT,
  refunded_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_checkout_status_created ON checkout_intents(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_checkout_user ON checkout_intents(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS point_holds (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  intent_id TEXT NOT NULL,
  user_id INTEGER NOT NULL,
  club_id INTEGER,
  point_type TEXT NOT NULL,
  points INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'held',
  funding_owner TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(intent_id) REFERENCES checkout_intents(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_point_hold_unique ON point_holds(intent_id,point_type);

CREATE TABLE IF NOT EXISTS commerce_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_key TEXT NOT NULL UNIQUE,
  provider TEXT NOT NULL,
  event_type TEXT NOT NULL,
  checkout_intent_id TEXT,
  commerce_order_id TEXT,
  payload_json TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- v0.8 Club membership + member benefit center
CREATE TABLE IF NOT EXISTS club_member_tiers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  club_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  rank INTEGER NOT NULL DEFAULT 0,
  min_activity_spend REAL NOT NULL DEFAULT 0,
  min_activity_count INTEGER NOT NULL DEFAULT 0,
  qualification_mode TEXT NOT NULL DEFAULT 'ANY',
  benefits_json TEXT NOT NULL DEFAULT '[]',
  -- 装备商城会员折扣率：1.0 = 无折扣，0.9 = 九折。NULL = 尚未配置（由迁移按等级回填）。
  gear_discount REAL,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(club_id,name),
  FOREIGN KEY(club_id) REFERENCES clubs(id)
);
CREATE INDEX IF NOT EXISTS idx_member_tiers_club_rank ON club_member_tiers(club_id,rank);

CREATE TABLE IF NOT EXISTS member_benefits (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_type TEXT NOT NULL,
  owner_club_id INTEGER,
  target_club_id INTEGER,
  title TEXT NOT NULL,
  description TEXT,
  benefit_type TEXT NOT NULL DEFAULT 'gift',
  points_type TEXT NOT NULL,
  points_cost INTEGER NOT NULL DEFAULT 0,
  cash_value REAL NOT NULL DEFAULT 0,
  stock INTEGER,
  status TEXT NOT NULL DEFAULT 'active',
  rules_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(owner_club_id) REFERENCES clubs(id),
  FOREIGN KEY(target_club_id) REFERENCES clubs(id)
);
CREATE INDEX IF NOT EXISTS idx_member_benefits_scope ON member_benefits(status,owner_type,target_club_id);

CREATE TABLE IF NOT EXISTS benefit_redemptions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  benefit_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  display_club_id INTEGER NOT NULL,
  point_type TEXT NOT NULL,
  points_spent INTEGER NOT NULL,
  funding_owner TEXT NOT NULL,
  cash_value REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'issued',
  voucher_code TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  used_at TEXT,
  held_checkout_id TEXT,
  used_order_kind TEXT,
  used_order_id TEXT,
  UNIQUE(voucher_code),
  FOREIGN KEY(benefit_id) REFERENCES member_benefits(id),
  FOREIGN KEY(user_id) REFERENCES users(id),
  FOREIGN KEY(display_club_id) REFERENCES clubs(id)
);
CREATE INDEX IF NOT EXISTS idx_benefit_redemptions_user ON benefit_redemptions(user_id,display_club_id,created_at DESC);

CREATE TABLE IF NOT EXISTS ai_credit_adjustment_debt (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  club_id INTEGER NOT NULL,
  amount INTEGER NOT NULL,
  source_type TEXT,
  source_id TEXT,
  note TEXT,
  resolved INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(club_id) REFERENCES clubs(id)
);



-- v0.15 real payment account routing
CREATE TABLE IF NOT EXISTS payment_accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scope_type TEXT NOT NULL,
  scope_id INTEGER,
  provider TEXT NOT NULL DEFAULT 'local',
  channel TEXT NOT NULL DEFAULT 'mock',
  merchant_id TEXT,
  app_id TEXT,
  credential_ref TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_payment_accounts_scope ON payment_accounts(scope_type,scope_id,enabled);

CREATE TABLE IF NOT EXISTS payment_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_key TEXT NOT NULL UNIQUE,
  provider TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payment_account_id INTEGER,
  checkout_intent_id TEXT,
  merchant_order_no TEXT,
  provider_transaction_id TEXT,
  verified INTEGER NOT NULL DEFAULT 0,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_payment_events_checkout ON payment_events(checkout_intent_id,created_at DESC);

-- v0.10 payment/refund lifecycle
CREATE TABLE IF NOT EXISTS payment_attempts (
  id TEXT PRIMARY KEY,
  checkout_intent_id TEXT NOT NULL,
  provider TEXT NOT NULL,
  payment_account_id INTEGER,
  channel TEXT,
  merchant_order_no TEXT,
  provider_payment_id TEXT,
  status TEXT NOT NULL DEFAULT 'processing',
  amount REAL NOT NULL DEFAULT 0,
  failure_reason TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  payment_action_json TEXT NOT NULL DEFAULT '{}',
  provider_payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(checkout_intent_id) REFERENCES checkout_intents(id)
);
CREATE INDEX IF NOT EXISTS idx_payment_attempt_checkout ON payment_attempts(checkout_intent_id,created_at DESC);

CREATE TABLE IF NOT EXISTS refund_requests (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  checkout_intent_id TEXT,
  registration_id INTEGER,
  gear_order_id INTEGER,
  participant_id INTEGER,
  refund_scope TEXT NOT NULL DEFAULT 'full',
  user_id INTEGER NOT NULL,
  club_id INTEGER,
  status TEXT NOT NULL DEFAULT 'requested',
  reason TEXT,
  decision_note TEXT,
  cash_amount REAL NOT NULL DEFAULT 0,
  original_cash_amount REAL NOT NULL DEFAULT 0,
  refund_percent REAL,
  retained_cash_amount REAL NOT NULL DEFAULT 0,
  policy_snapshot_json TEXT,
  policy_label TEXT,
  allocated_original_amount REAL NOT NULL DEFAULT 0,
  allocated_club_points INTEGER NOT NULL DEFAULT 0,
  allocated_gear_points INTEGER NOT NULL DEFAULT 0,
  allocated_club_point_discount REAL NOT NULL DEFAULT 0,
  allocated_platform_point_subsidy REAL NOT NULL DEFAULT 0,
  allocated_club_benefit_discount REAL NOT NULL DEFAULT 0,
  allocated_platform_benefit_subsidy REAL NOT NULL DEFAULT 0,
  allocated_club_points_earned INTEGER NOT NULL DEFAULT 0,
  commerce_order_id TEXT,
  after_sales_case_id TEXT,
  commerce_refund_status TEXT NOT NULL DEFAULT 'not_required',
  commerce_refund_id TEXT,
  commerce_refund_error TEXT,
  provider TEXT,
  payment_account_id INTEGER,
  provider_refund_no TEXT,
  provider_refund_id TEXT,
  provider_status TEXT,
  provider_payload_json TEXT NOT NULL DEFAULT '{}',
  requested_by TEXT,
  approved_by TEXT,
  approved_at TEXT,
  refunded_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_refund_requests_club ON refund_requests(club_id,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_refund_requests_registration ON refund_requests(registration_id);
CREATE INDEX IF NOT EXISTS idx_refund_requests_gear_order ON refund_requests(gear_order_id);

-- v0.13 occurrence execution center
CREATE TABLE IF NOT EXISTS occurrence_execution_settings (
  occurrence_id INTEGER PRIMARY KEY,
  club_id INTEGER NOT NULL,
  meeting_time TEXT,
  meeting_location TEXT,
  emergency_phone TEXT,
  leader_note TEXT,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(occurrence_id) REFERENCES activity_occurrences(id),
  FOREIGN KEY(club_id) REFERENCES clubs(id)
);

CREATE TABLE IF NOT EXISTS occurrence_leaders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  occurrence_id INTEGER NOT NULL,
  club_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  phone TEXT,
  role TEXT NOT NULL DEFAULT '领队',
  note TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(occurrence_id) REFERENCES activity_occurrences(id),
  FOREIGN KEY(club_id) REFERENCES clubs(id)
);
CREATE INDEX IF NOT EXISTS idx_occurrence_leaders ON occurrence_leaders(occurrence_id);

-- v0.27 领队资源库
-- 此前每场活动只能手打领队「姓名 + 电话」，俱乐部没有自己的领队名册：
-- 既无法复用同一个人，也无法回答「这条线路以前是谁带的」。名册是推荐与排班的前提。
CREATE TABLE IF NOT EXISTS club_leaders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  club_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  phone TEXT,
  role TEXT NOT NULL DEFAULT '领队',
  -- 擅长方向（活动类型标签，JSON 字符串数组），用于按活动自动推荐领队
  specialties TEXT NOT NULL DEFAULT '[]',
  base_city TEXT,
  -- 领队头像。只存经 /api/club/{club_id}/leaders/{id}/avatar.{ext} 代理过的路径，
  -- 且扩展名必须带（反解磁盘文件时靠它定位）。绝不存 /static/uploads 裸链 ——
  -- 生产环境该路径被 security_v025 封死，存进去就是一条打不开的地址。
  avatar_url TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  note TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(club_id) REFERENCES clubs(id)
);
CREATE INDEX IF NOT EXISTS idx_club_leaders ON club_leaders(club_id,status);

CREATE TABLE IF NOT EXISTS execution_groups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  occurrence_id INTEGER NOT NULL,
  club_id INTEGER NOT NULL,
  group_type TEXT NOT NULL,
  name TEXT NOT NULL,
  leader_name TEXT,
  leader_phone TEXT,
  capacity INTEGER,
  note TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(occurrence_id) REFERENCES activity_occurrences(id),
  FOREIGN KEY(club_id) REFERENCES clubs(id)
);
CREATE INDEX IF NOT EXISTS idx_execution_groups ON execution_groups(occurrence_id,group_type);

CREATE TABLE IF NOT EXISTS participant_group_assignments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  occurrence_id INTEGER NOT NULL,
  participant_id INTEGER NOT NULL,
  group_id INTEGER NOT NULL,
  group_type TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(participant_id,group_type),
  FOREIGN KEY(occurrence_id) REFERENCES activity_occurrences(id),
  FOREIGN KEY(participant_id) REFERENCES registration_participants(id),
  FOREIGN KEY(group_id) REFERENCES execution_groups(id)
);
CREATE INDEX IF NOT EXISTS idx_group_assignments_occurrence ON participant_group_assignments(occurrence_id,group_type);

CREATE TABLE IF NOT EXISTS activity_notices (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  occurrence_id INTEGER NOT NULL,
  club_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  audience TEXT NOT NULL DEFAULT 'all',
  channel TEXT NOT NULL DEFAULT 'manual',
  status TEXT NOT NULL DEFAULT 'draft',
  sent_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(occurrence_id) REFERENCES activity_occurrences(id),
  FOREIGN KEY(club_id) REFERENCES clubs(id)
);
CREATE INDEX IF NOT EXISTS idx_activity_notices ON activity_notices(occurrence_id,status);

CREATE TABLE IF NOT EXISTS participant_checkins (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  participant_id INTEGER NOT NULL,
  occurrence_id INTEGER NOT NULL,
  checkin_type TEXT NOT NULL DEFAULT 'departure',
  status TEXT NOT NULL DEFAULT 'pending',
  checked_at TEXT,
  note TEXT,
  actor_type TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(participant_id,checkin_type),
  FOREIGN KEY(participant_id) REFERENCES registration_participants(id),
  FOREIGN KEY(occurrence_id) REFERENCES activity_occurrences(id)
);
CREATE INDEX IF NOT EXISTS idx_checkins_occurrence ON participant_checkins(occurrence_id,status);

CREATE TABLE IF NOT EXISTS execution_event_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  occurrence_id INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  actor_type TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(occurrence_id) REFERENCES activity_occurrences(id)
);
CREATE INDEX IF NOT EXISTS idx_execution_events ON execution_event_logs(occurrence_id,created_at DESC);


-- v0.19 Supplier / Procurement / Inbound Inventory
CREATE TABLE IF NOT EXISTS suppliers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  contact_name TEXT,
  phone TEXT,
  email TEXT,
  address TEXT,
  payment_terms TEXT,
  payment_term_days INTEGER NOT NULL DEFAULT 0,
  lead_time_days INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active',
  note TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS product_suppliers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER NOT NULL,
  supplier_id INTEGER NOT NULL,
  supplier_sku TEXT,
  purchase_price REAL NOT NULL DEFAULT 0,
  min_order_qty INTEGER NOT NULL DEFAULT 1,
  lead_time_days INTEGER NOT NULL DEFAULT 0,
  is_primary INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(product_id,supplier_id),
  FOREIGN KEY(product_id) REFERENCES products(id),
  FOREIGN KEY(supplier_id) REFERENCES suppliers(id)
);
CREATE INDEX IF NOT EXISTS idx_product_suppliers_supplier ON product_suppliers(supplier_id,status);

CREATE TABLE IF NOT EXISTS purchase_orders (
  id TEXT PRIMARY KEY,
  supplier_id INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft',
  currency_code TEXT NOT NULL DEFAULT 'cny',
  subtotal REAL NOT NULL DEFAULT 0,
  total_amount REAL NOT NULL DEFAULT 0,
  expected_at TEXT,
  approved_by TEXT,
  approved_at TEXT,
  ordered_at TEXT,
  received_at TEXT,
  cancelled_at TEXT,
  note TEXT,
  created_by TEXT NOT NULL DEFAULT 'platform',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(supplier_id) REFERENCES suppliers(id)
);
CREATE INDEX IF NOT EXISTS idx_purchase_orders_supplier_status ON purchase_orders(supplier_id,status,created_at DESC);

CREATE TABLE IF NOT EXISTS purchase_order_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  purchase_order_id TEXT NOT NULL,
  product_id INTEGER NOT NULL,
  supplier_sku TEXT,
  quantity_ordered INTEGER NOT NULL,
  quantity_received INTEGER NOT NULL DEFAULT 0,
  unit_cost REAL NOT NULL DEFAULT 0,
  line_total REAL NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(purchase_order_id,product_id),
  FOREIGN KEY(purchase_order_id) REFERENCES purchase_orders(id),
  FOREIGN KEY(product_id) REFERENCES products(id)
);
CREATE INDEX IF NOT EXISTS idx_purchase_order_items_product ON purchase_order_items(product_id);

CREATE TABLE IF NOT EXISTS purchase_receipts (
  id TEXT PRIMARY KEY,
  purchase_order_id TEXT NOT NULL,
  supplier_id INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'received',
  received_by TEXT NOT NULL DEFAULT 'platform',
  note TEXT,
  total_cost REAL NOT NULL DEFAULT 0,
  received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(purchase_order_id) REFERENCES purchase_orders(id),
  FOREIGN KEY(supplier_id) REFERENCES suppliers(id)
);
CREATE INDEX IF NOT EXISTS idx_purchase_receipts_po ON purchase_receipts(purchase_order_id,received_at DESC);

CREATE TABLE IF NOT EXISTS purchase_receipt_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  receipt_id TEXT NOT NULL,
  purchase_order_item_id INTEGER NOT NULL,
  product_id INTEGER NOT NULL,
  quantity INTEGER NOT NULL,
  unit_cost REAL NOT NULL DEFAULT 0,
  line_total REAL NOT NULL DEFAULT 0,
  ordered_unit_cost REAL NOT NULL DEFAULT 0,
  price_variance REAL NOT NULL DEFAULT 0,
  stock_before INTEGER NOT NULL,
  stock_after INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(receipt_id) REFERENCES purchase_receipts(id),
  FOREIGN KEY(purchase_order_item_id) REFERENCES purchase_order_items(id),
  FOREIGN KEY(product_id) REFERENCES products(id)
);

CREATE TABLE IF NOT EXISTS inventory_movements (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER NOT NULL,
  movement_type TEXT NOT NULL,
  quantity_delta INTEGER NOT NULL,
  stock_before INTEGER NOT NULL,
  stock_after INTEGER NOT NULL,
  unit_cost REAL NOT NULL DEFAULT 0,
  total_cost REAL NOT NULL DEFAULT 0,
  reference_type TEXT,
  reference_id TEXT,
  note TEXT,
  actor_type TEXT NOT NULL DEFAULT 'system',
  idempotency_key TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(product_id) REFERENCES products(id)
);
CREATE INDEX IF NOT EXISTS idx_inventory_movements_product ON inventory_movements(product_id,id DESC);
CREATE INDEX IF NOT EXISTS idx_inventory_movements_reference ON inventory_movements(reference_type,reference_id);


-- v0.20 Warehouse & Inventory Control (WMS Lite)
CREATE TABLE IF NOT EXISTS warehouses (
  id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL, address TEXT,
  status TEXT NOT NULL DEFAULT 'active', is_default INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS warehouse_locations (
  id INTEGER PRIMARY KEY AUTOINCREMENT, warehouse_id INTEGER NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL, zone_type TEXT NOT NULL DEFAULT 'sellable',
  status TEXT NOT NULL DEFAULT 'active', is_default INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(warehouse_id,code), FOREIGN KEY(warehouse_id) REFERENCES warehouses(id)
);
CREATE TABLE IF NOT EXISTS warehouse_inventory (
  id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL, location_id INTEGER NOT NULL, on_hand INTEGER NOT NULL DEFAULT 0, reserved INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(product_id,location_id), FOREIGN KEY(product_id) REFERENCES products(id), FOREIGN KEY(location_id) REFERENCES warehouse_locations(id)
);
CREATE TABLE IF NOT EXISTS warehouse_reservations (
  id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL, product_id INTEGER NOT NULL, quantity INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'reserved', allocations_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(order_id,product_id), FOREIGN KEY(order_id) REFERENCES gear_orders(id), FOREIGN KEY(product_id) REFERENCES products(id)
);
CREATE TABLE IF NOT EXISTS warehouse_movements (
  id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL, movement_type TEXT NOT NULL, quantity INTEGER NOT NULL, from_location_id INTEGER, to_location_id INTEGER, reference_type TEXT, reference_id TEXT, note TEXT, actor_type TEXT NOT NULL DEFAULT 'system', idempotency_key TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(product_id) REFERENCES products(id), FOREIGN KEY(from_location_id) REFERENCES warehouse_locations(id), FOREIGN KEY(to_location_id) REFERENCES warehouse_locations(id)
);
CREATE TABLE IF NOT EXISTS warehouse_tasks (
  id TEXT PRIMARY KEY, order_id INTEGER NOT NULL, warehouse_id INTEGER NOT NULL, task_type TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', note TEXT, picked_at TEXT, packed_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(order_id,task_type), FOREIGN KEY(order_id) REFERENCES gear_orders(id), FOREIGN KEY(warehouse_id) REFERENCES warehouses(id)
);
CREATE INDEX IF NOT EXISTS idx_warehouse_inventory_product ON warehouse_inventory(product_id);
CREATE INDEX IF NOT EXISTS idx_warehouse_reservations_order ON warehouse_reservations(order_id,status);
CREATE INDEX IF NOT EXISTS idx_warehouse_movements_product ON warehouse_movements(product_id,id DESC);
CREATE INDEX IF NOT EXISTS idx_warehouse_tasks_status ON warehouse_tasks(status,created_at DESC);


-- v0.21 Supplier Settlement & Merchandise Profit
CREATE TABLE IF NOT EXISTS supplier_payables (
  id TEXT PRIMARY KEY,
  supplier_id INTEGER NOT NULL,
  purchase_order_id TEXT,
  receipt_id TEXT NOT NULL UNIQUE,
  original_amount REAL NOT NULL DEFAULT 0,
  credit_applied REAL NOT NULL DEFAULT 0,
  paid_amount REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'open',
  due_at TEXT,
  note TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(supplier_id) REFERENCES suppliers(id),
  FOREIGN KEY(purchase_order_id) REFERENCES purchase_orders(id),
  FOREIGN KEY(receipt_id) REFERENCES purchase_receipts(id)
);
CREATE INDEX IF NOT EXISTS idx_supplier_payables_supplier_status ON supplier_payables(supplier_id,status,due_at);
CREATE TABLE IF NOT EXISTS supplier_payments (
  id TEXT PRIMARY KEY, supplier_id INTEGER NOT NULL, amount REAL NOT NULL, payment_ref TEXT NOT NULL UNIQUE,
  payment_method TEXT, note TEXT, paid_by TEXT NOT NULL DEFAULT 'platform', paid_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(supplier_id) REFERENCES suppliers(id)
);
CREATE INDEX IF NOT EXISTS idx_supplier_payments_supplier ON supplier_payments(supplier_id,paid_at DESC);
CREATE TABLE IF NOT EXISTS supplier_payment_allocations (
  id INTEGER PRIMARY KEY AUTOINCREMENT, payment_id TEXT NOT NULL, payable_id TEXT NOT NULL, amount REAL NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(payment_id,payable_id),
  FOREIGN KEY(payment_id) REFERENCES supplier_payments(id), FOREIGN KEY(payable_id) REFERENCES supplier_payables(id)
);
CREATE TABLE IF NOT EXISTS supplier_credits (
  id TEXT PRIMARY KEY, supplier_id INTEGER NOT NULL, source_type TEXT NOT NULL, source_id TEXT NOT NULL, amount REAL NOT NULL,
  used_amount REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'available', note TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(source_type,source_id), FOREIGN KEY(supplier_id) REFERENCES suppliers(id)
);
CREATE INDEX IF NOT EXISTS idx_supplier_credits_supplier ON supplier_credits(supplier_id,status,created_at);
CREATE TABLE IF NOT EXISTS supplier_credit_allocations (
  id INTEGER PRIMARY KEY AUTOINCREMENT, credit_id TEXT NOT NULL, payable_id TEXT NOT NULL, amount REAL NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(credit_id,payable_id),
  FOREIGN KEY(credit_id) REFERENCES supplier_credits(id), FOREIGN KEY(payable_id) REFERENCES supplier_payables(id)
);
CREATE TABLE IF NOT EXISTS purchase_returns (
  id TEXT PRIMARY KEY, supplier_id INTEGER NOT NULL, purchase_order_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft',
  total_amount REAL NOT NULL DEFAULT 0, credit_amount REAL NOT NULL DEFAULT 0, note TEXT, created_by TEXT NOT NULL DEFAULT 'platform',
  approved_by TEXT, approved_at TEXT, shipped_at TEXT, credited_at TEXT, cancelled_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(supplier_id) REFERENCES suppliers(id), FOREIGN KEY(purchase_order_id) REFERENCES purchase_orders(id)
);
CREATE INDEX IF NOT EXISTS idx_purchase_returns_supplier_status ON purchase_returns(supplier_id,status,created_at DESC);
CREATE TABLE IF NOT EXISTS purchase_return_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT, purchase_return_id TEXT NOT NULL, purchase_order_item_id INTEGER NOT NULL,
  product_id INTEGER NOT NULL, quantity INTEGER NOT NULL, unit_cost REAL NOT NULL DEFAULT 0, line_total REAL NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(purchase_return_id) REFERENCES purchase_returns(id),
  FOREIGN KEY(purchase_order_item_id) REFERENCES purchase_order_items(id), FOREIGN KEY(product_id) REFERENCES products(id)
);
CREATE INDEX IF NOT EXISTS idx_purchase_return_items_product ON purchase_return_items(product_id);

-- v0.23 · Platform Club & AI Credits commercialization
CREATE TABLE IF NOT EXISTS ai_credit_plans (
  code TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  monthly_fee REAL NOT NULL DEFAULT 0,
  monthly_credits INTEGER NOT NULL DEFAULT 0,
  description TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active',
  sort_order INTEGER NOT NULL DEFAULT 100,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ai_credit_topup_packages (
  code TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  amount REAL NOT NULL DEFAULT 0,
  credits INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active',
  sort_order INTEGER NOT NULL DEFAULT 100,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS club_ai_subscriptions (
  club_id INTEGER PRIMARY KEY,
  plan_code TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  current_period_start TEXT,
  current_period_end TEXT,
  auto_renew INTEGER NOT NULL DEFAULT 1,
  monthly_fee_snapshot REAL NOT NULL DEFAULT 0,
  monthly_credits_snapshot INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(club_id) REFERENCES clubs(id),
  FOREIGN KEY(plan_code) REFERENCES ai_credit_plans(code)
);

CREATE TABLE IF NOT EXISTS ai_credit_orders (
  id TEXT PRIMARY KEY,
  club_id INTEGER NOT NULL,
  order_type TEXT NOT NULL,
  plan_code TEXT,
  package_code TEXT,
  credits INTEGER NOT NULL DEFAULT 0,
  amount REAL NOT NULL DEFAULT 0,
  period_key TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  payment_ref TEXT UNIQUE,
  note TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  paid_at TEXT,
  FOREIGN KEY(club_id) REFERENCES clubs(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_credit_subscription_period
  ON ai_credit_orders(club_id,order_type,period_key) WHERE order_type='subscription';
CREATE INDEX IF NOT EXISTS idx_ai_credit_orders_club_status ON ai_credit_orders(club_id,status,created_at DESC);

-- ── 商品规格与图集（v0.27）───────────────────────────────
-- 两张表的语义刻意对齐 Medusa：product_variants ↔ product.variants、
-- product_images ↔ product.images。将来 COMMERCE_PROVIDER 切到 medusa 时
-- 只需要换数据源，前端字段名不用动。
--
-- 库存从此下沉到规格：product_variants.stock 是真源，
-- products.stock 退化为 SUM(variant.stock) 的冗余值（列表排序、采购预警读它）。
-- 没有规格的商品在迁移时会自动生成一个 is_default=1 的「默认」规格承接原库存，
-- 所以旧代码里所有按 product_id 读库存的地方语义不变。

CREATE TABLE IF NOT EXISTS product_variants (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  sku TEXT,
  price REAL,
  stock INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active',
  sort INTEGER NOT NULL DEFAULT 0,
  is_default INTEGER NOT NULL DEFAULT 0,
  commerce_variant_id TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_product_variants_product ON product_variants(product_id,sort,id);

CREATE TABLE IF NOT EXISTS product_images (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER NOT NULL,
  url TEXT NOT NULL,
  sort INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_product_images_product ON product_images(product_id,sort,id);

-- v0.26 · 俱乐部「装备上下架」申请
-- products 表没有 club 归属列，商品归总平台所有：俱乐部不能直接改 status，
-- 否则俱乐部端就能绕过总平台把商品从 C 端商城里摘掉。改成俱乐部提交申请、
-- 总平台一键处理，两边看到的是同一条记录。
CREATE TABLE IF NOT EXISTS club_product_visibility_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  club_id INTEGER NOT NULL,
  product_id INTEGER NOT NULL,
  action TEXT NOT NULL,                     -- on=申请上架 / off=申请下架
  note TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',   -- pending / approved / rejected
  decided_note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  decided_at TEXT,
  FOREIGN KEY(club_id) REFERENCES clubs(id),
  FOREIGN KEY(product_id) REFERENCES products(id)
);
CREATE INDEX IF NOT EXISTS idx_club_prod_vis_club ON club_product_visibility_requests(club_id,status,id DESC);
CREATE INDEX IF NOT EXISTS idx_club_prod_vis_product ON club_product_visibility_requests(product_id,id DESC);
