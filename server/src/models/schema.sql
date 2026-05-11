-- Sopdrop Database Schema
-- A Houdini asset registry focused on nodes, HDAs, and versioning

-- ============================================
-- USERS & AUTHENTICATION
-- ============================================

CREATE TABLE IF NOT EXISTS users (
  id SERIAL PRIMARY KEY,
  username VARCHAR(50) UNIQUE NOT NULL,
  email VARCHAR(255) UNIQUE NOT NULL,
  display_name VARCHAR(100),
  avatar_url TEXT,
  bio TEXT,
  website VARCHAR(255),

  -- Auth
  password_hash VARCHAR(255),  -- For local auth (optional)
  google_id VARCHAR(255) UNIQUE,  -- For OAuth (optional)

  -- Status
  is_verified BOOLEAN DEFAULT false,
  is_admin BOOLEAN DEFAULT false,

  -- Stats
  asset_count INTEGER DEFAULT 0,
  download_count INTEGER DEFAULT 0,

  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

-- API Tokens for Houdini client auth
CREATE TABLE IF NOT EXISTS api_tokens (
  id SERIAL PRIMARY KEY,
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  token_hash VARCHAR(255) NOT NULL,
  name VARCHAR(100) NOT NULL,  -- e.g., "Work Laptop", "Home Desktop"

  -- Permissions (extensible)
  scopes TEXT[] DEFAULT ARRAY['read', 'write'],

  -- Usage tracking
  last_used_at TIMESTAMP,
  last_used_ip VARCHAR(45),

  -- Expiration (null = never expires)
  expires_at TIMESTAMP,

  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_api_tokens_user_id ON api_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_api_tokens_hash ON api_tokens(token_hash);

-- ============================================
-- ASSETS (Houdini Nodes & HDAs)
-- ============================================

CREATE TABLE IF NOT EXISTS assets (
  id SERIAL PRIMARY KEY,
  asset_id UUID UNIQUE NOT NULL DEFAULT gen_random_uuid(),

  -- Identity
  name VARCHAR(100) NOT NULL,
  slug VARCHAR(100) NOT NULL,  -- URL-safe: 'scatter-points'
  owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,

  -- Full slug is owner_username/slug (computed, not stored)

  -- Type
  asset_type VARCHAR(20) NOT NULL CHECK (asset_type IN ('node', 'hda', 'vex', 'collection')),
  houdini_context VARCHAR(20) CHECK (houdini_context IN ('sop', 'vop', 'lop', 'obj', 'cop', 'top', 'chop', 'dop', 'shop', 'rop', 'vex')),

  -- Description
  description TEXT,
  readme TEXT,  -- Full markdown documentation

  -- Licensing
  license VARCHAR(50) NOT NULL DEFAULT 'mit',
  license_url TEXT,

  -- Compatibility
  min_houdini_version VARCHAR(20),  -- e.g., '19.5'
  max_houdini_version VARCHAR(20),  -- null = no max

  -- Organization
  tags TEXT[],

  -- Version tracking (points to latest)
  latest_version_id INTEGER,  -- Will be FK after versions table created
  latest_version VARCHAR(20),

  -- Stats (counters, no social features)
  download_count INTEGER DEFAULT 0,

  -- Visibility
  is_public BOOLEAN DEFAULT true,
  is_deprecated BOOLEAN DEFAULT false,
  deprecated_message TEXT,

  -- Metadata (flexible JSONB for future fields)
  metadata JSONB DEFAULT '{}',

  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

  -- Unique constraint: one asset name per user
  UNIQUE(owner_id, slug)
);

CREATE INDEX IF NOT EXISTS idx_assets_owner ON assets(owner_id);
CREATE INDEX IF NOT EXISTS idx_assets_slug ON assets(slug);
CREATE INDEX IF NOT EXISTS idx_assets_type ON assets(asset_type);
CREATE INDEX IF NOT EXISTS idx_assets_context ON assets(houdini_context);
CREATE INDEX IF NOT EXISTS idx_assets_tags ON assets USING GIN(tags);
CREATE INDEX IF NOT EXISTS idx_assets_public ON assets(is_public) WHERE is_public = true;

-- Full-text search index
CREATE INDEX IF NOT EXISTS idx_assets_search ON assets USING GIN(
  to_tsvector('english', coalesce(name, '') || ' ' || coalesce(description, '') || ' ' || coalesce(readme, ''))
);

-- ============================================
-- VERSIONS (Immutable once published)
-- ============================================

CREATE TABLE IF NOT EXISTS versions (
  id SERIAL PRIMARY KEY,
  version_id UUID UNIQUE NOT NULL DEFAULT gen_random_uuid(),
  asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,

  -- Semver version
  version VARCHAR(20) NOT NULL,  -- '1.0.0', '1.0.1', '2.0.0-beta'

  -- Files
  file_path TEXT NOT NULL,  -- /library/nodes/[uuid].cpio
  file_hash VARCHAR(64) NOT NULL,  -- SHA256 for integrity verification
  file_size INTEGER NOT NULL,

  -- Optional preview/thumbnail
  thumbnail_url TEXT,
  preview_url TEXT,

  -- Changelog
  changelog TEXT,

  -- Compatibility (can differ per version)
  min_houdini_version VARCHAR(20),
  max_houdini_version VARCHAR(20),

  -- Node info (extracted from .cpio)
  node_count INTEGER,
  node_names TEXT[],

  -- Stats
  download_count INTEGER DEFAULT 0,

  -- Immutable timestamp
  published_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  published_by INTEGER REFERENCES users(id),

  -- Versions are immutable - no updated_at

  UNIQUE(asset_id, version)
);

CREATE INDEX IF NOT EXISTS idx_versions_asset ON versions(asset_id);
CREATE INDEX IF NOT EXISTS idx_versions_version ON versions(version);
CREATE INDEX IF NOT EXISTS idx_versions_published ON versions(published_at);

-- Add FK from assets to versions (after versions table exists)
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE constraint_name = 'fk_assets_latest_version'
  ) THEN
    ALTER TABLE assets
      ADD CONSTRAINT fk_assets_latest_version
      FOREIGN KEY (latest_version_id) REFERENCES versions(id) ON DELETE SET NULL;
  END IF;
END $$;

-- ============================================
-- COLLECTIONS (Group related assets)
-- ============================================

CREATE TABLE IF NOT EXISTS collection_assets (
  id SERIAL PRIMARY KEY,
  collection_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  position INTEGER DEFAULT 0,  -- Order in collection
  added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

  UNIQUE(collection_id, asset_id)
);

CREATE INDEX IF NOT EXISTS idx_collection_assets_collection ON collection_assets(collection_id);

-- ============================================
-- INSTALL TRACKING (Optional - for sync)
-- ============================================

CREATE TABLE IF NOT EXISTS installs (
  id SERIAL PRIMARY KEY,
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  version_id INTEGER NOT NULL REFERENCES versions(id) ON DELETE CASCADE,

  -- Optional machine identifier (for multi-workstation tracking)
  machine_id VARCHAR(100),

  installed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

  UNIQUE(user_id, asset_id, machine_id)
);

CREATE INDEX IF NOT EXISTS idx_installs_user ON installs(user_id);
CREATE INDEX IF NOT EXISTS idx_installs_asset ON installs(asset_id);

-- ============================================
-- TEMPORARY SHARES (Ephemeral, like Quick Copy)
-- ============================================

CREATE TABLE IF NOT EXISTS temp_shares (
  id SERIAL PRIMARY KEY,
  share_code VARCHAR(10) UNIQUE NOT NULL,  -- e.g., 'TC-4B9X'

  -- File
  file_path TEXT NOT NULL,
  file_hash VARCHAR(64),

  -- Metadata
  name VARCHAR(255),
  houdini_context VARCHAR(20),
  node_count INTEGER,
  node_names TEXT[],

  -- Owner (optional - can be anonymous)
  created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,

  -- Expiration
  expires_at TIMESTAMP NOT NULL,

  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_temp_shares_code ON temp_shares(share_code);
CREATE INDEX IF NOT EXISTS idx_temp_shares_expires ON temp_shares(expires_at);

-- ============================================
-- ASSET DRAFTS (Hybrid publish workflow)
-- ============================================

-- Drafts are created from Houdini, completed in browser
CREATE TABLE IF NOT EXISTS asset_drafts (
  id SERIAL PRIMARY KEY,
  draft_id UUID UNIQUE NOT NULL DEFAULT gen_random_uuid(),
  owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,

  -- Package file
  file_path TEXT NOT NULL,
  file_size INTEGER,

  -- Metadata from package
  houdini_context VARCHAR(20),
  node_count INTEGER,
  node_names TEXT[],
  node_types TEXT[],
  houdini_version VARCHAR(50),
  has_hda_dependencies BOOLEAN DEFAULT false,
  dependencies JSONB DEFAULT '[]',

  -- User-provided metadata (filled in browser)
  name VARCHAR(100),
  description TEXT,
  tags TEXT[],
  license VARCHAR(50) DEFAULT 'mit',
  thumbnail_url TEXT,

  -- Expiration (24 hours from creation)
  expires_at TIMESTAMP NOT NULL,

  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_asset_drafts_owner ON asset_drafts(owner_id);
CREATE INDEX IF NOT EXISTS idx_asset_drafts_expires ON asset_drafts(expires_at);

-- ============================================
-- LICENSE TYPES (Reference data)
-- ============================================

CREATE TABLE IF NOT EXISTS license_types (
  id VARCHAR(50) PRIMARY KEY,  -- 'mit', 'apache-2.0', etc.
  name VARCHAR(100) NOT NULL,
  url TEXT,
  description TEXT,
  is_osi_approved BOOLEAN DEFAULT false
);

-- Seed common licenses
INSERT INTO license_types (id, name, url, is_osi_approved) VALUES
  ('mit', 'MIT License', 'https://opensource.org/licenses/MIT', true),
  ('apache-2.0', 'Apache License 2.0', 'https://opensource.org/licenses/Apache-2.0', true),
  ('gpl-3.0', 'GNU GPL v3', 'https://www.gnu.org/licenses/gpl-3.0.html', true),
  ('bsd-3-clause', 'BSD 3-Clause', 'https://opensource.org/licenses/BSD-3-Clause', true),
  ('cc-by-4.0', 'Creative Commons BY 4.0', 'https://creativecommons.org/licenses/by/4.0/', false),
  ('cc-by-sa-4.0', 'Creative Commons BY-SA 4.0', 'https://creativecommons.org/licenses/by-sa/4.0/', false),
  ('cc0-1.0', 'CC0 1.0 (Public Domain)', 'https://creativecommons.org/publicdomain/zero/1.0/', false),
  ('unlicense', 'The Unlicense', 'https://unlicense.org/', true),
  ('proprietary', 'Proprietary', NULL, false)
ON CONFLICT (id) DO NOTHING;

-- ============================================
-- SECURITY: Audit Logs
-- ============================================

CREATE TABLE IF NOT EXISTS audit_logs (
  id SERIAL PRIMARY KEY,
  event_type VARCHAR(50) NOT NULL,
  event_category VARCHAR(20) NOT NULL,  -- AUTH, ASSET, ADMIN, ABUSE, SECURITY
  actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  actor_ip INET,
  target_type VARCHAR(50),
  target_id VARCHAR(255),
  details JSONB,
  request_id UUID,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_event ON audit_logs(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_logs_category ON audit_logs(event_category);
CREATE INDEX IF NOT EXISTS idx_audit_logs_actor ON audit_logs(actor_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_created ON audit_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_logs_request ON audit_logs(request_id);

-- ============================================
-- SECURITY: Abuse Reports
-- ============================================

CREATE TABLE IF NOT EXISTS abuse_reports (
  id SERIAL PRIMARY KEY,
  target_type VARCHAR(20) NOT NULL,  -- 'asset' or 'user'
  target_id VARCHAR(255) NOT NULL,   -- asset slug or username
  reason VARCHAR(50) NOT NULL,       -- malware, copyright, impersonation, spam, other
  details TEXT,
  reporter_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  reporter_email VARCHAR(255),
  reporter_ip INET,
  status VARCHAR(20) DEFAULT 'pending',  -- pending, resolved, dismissed, actioned
  resolved_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  resolution_notes TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  resolved_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_abuse_reports_status ON abuse_reports(status);
CREATE INDEX IF NOT EXISTS idx_abuse_reports_target ON abuse_reports(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_abuse_reports_created ON abuse_reports(created_at);

-- ============================================
-- SECURITY: Email Verification (add columns to users)
-- ============================================

-- Add email verification columns if they don't exist
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'users' AND column_name = 'email_verified'
  ) THEN
    ALTER TABLE users ADD COLUMN email_verified BOOLEAN DEFAULT FALSE;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'users' AND column_name = 'email_verification_token'
  ) THEN
    ALTER TABLE users ADD COLUMN email_verification_token VARCHAR(255);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'users' AND column_name = 'email_verification_expires'
  ) THEN
    ALTER TABLE users ADD COLUMN email_verification_expires TIMESTAMPTZ;
  END IF;
END $$;

-- ============================================
-- INVITE CODES (Closed Beta)
-- ============================================

CREATE TABLE IF NOT EXISTS invite_codes (
  id SERIAL PRIMARY KEY,
  code VARCHAR(20) UNIQUE NOT NULL,
  created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  used_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  max_uses INTEGER DEFAULT 1,  -- How many times this code can be used
  use_count INTEGER DEFAULT 0, -- How many times it has been used
  note TEXT,                   -- Admin note about who this is for
  expires_at TIMESTAMPTZ,      -- Optional expiration
  created_at TIMESTAMPTZ DEFAULT NOW(),
  used_at TIMESTAMPTZ          -- When it was first used
);

CREATE INDEX IF NOT EXISTS idx_invite_codes_code ON invite_codes(code);
CREATE INDEX IF NOT EXISTS idx_invite_codes_created_by ON invite_codes(created_by);

-- Add invited_by to users table
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'users' AND column_name = 'invited_by'
  ) THEN
    ALTER TABLE users ADD COLUMN invited_by INTEGER REFERENCES users(id) ON DELETE SET NULL;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'users' AND column_name = 'invite_code_used'
  ) THEN
    ALTER TABLE users ADD COLUMN invite_code_used VARCHAR(20);
  END IF;

  -- OAuth provider IDs
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'users' AND column_name = 'discord_id'
  ) THEN
    ALTER TABLE users ADD COLUMN discord_id VARCHAR(255) UNIQUE;
  END IF;
END $$;

-- ============================================
-- CLEANUP FUNCTIONS
-- ============================================

CREATE OR REPLACE FUNCTION cleanup_expired_shares()
RETURNS INTEGER AS $$
DECLARE
  deleted_count INTEGER;
BEGIN
  DELETE FROM temp_shares WHERE expires_at < NOW();
  GET DIAGNOSTICS deleted_count = ROW_COUNT;
  RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- Cleanup old audit logs (keep 90 days)
CREATE OR REPLACE FUNCTION cleanup_old_audit_logs()
RETURNS INTEGER AS $$
DECLARE
  deleted_count INTEGER;
BEGIN
  DELETE FROM audit_logs WHERE created_at < NOW() - INTERVAL '90 days';
  GET DIAGNOSTICS deleted_count = ROW_COUNT;
  RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- ASSET MEDIA (Images/Videos for previews)
-- ============================================

-- Add media column to assets if it doesn't exist
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'assets' AND column_name = 'media'
  ) THEN
    ALTER TABLE assets ADD COLUMN media JSONB DEFAULT '[]';
  END IF;
END $$;

-- Add houdini_version to versions (the version Houdini it was created in)
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'versions' AND column_name = 'houdini_version'
  ) THEN
    ALTER TABLE versions ADD COLUMN houdini_version VARCHAR(20);
  END IF;
END $$;

-- Add code column to versions (for asCode() Python preview)
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'versions' AND column_name = 'code'
  ) THEN
    ALTER TABLE versions ADD COLUMN code TEXT;
  END IF;
END $$;

-- Add houdini_license to versions (for HDA compatibility: indie, core, fx)
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'versions' AND column_name = 'houdini_license'
  ) THEN
    ALTER TABLE versions ADD COLUMN houdini_license VARCHAR(20);
  END IF;
END $$;

-- ============================================
-- FAVORITES
-- ============================================

CREATE TABLE IF NOT EXISTS favorites (
  id SERIAL PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(user_id, asset_id)
);

CREATE INDEX IF NOT EXISTS idx_favorites_user ON favorites(user_id);
CREATE INDEX IF NOT EXISTS idx_favorites_asset ON favorites(asset_id);

-- ============================================
-- COMMENTS
-- ============================================

CREATE TABLE IF NOT EXISTS comments (
  id SERIAL PRIMARY KEY,
  comment_id UUID UNIQUE NOT NULL DEFAULT gen_random_uuid(),
  asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  parent_id INTEGER REFERENCES comments(id) ON DELETE CASCADE,  -- For replies
  content TEXT NOT NULL,
  is_edited BOOLEAN DEFAULT false,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_comments_asset ON comments(asset_id);
CREATE INDEX IF NOT EXISTS idx_comments_user ON comments(user_id);
CREATE INDEX IF NOT EXISTS idx_comments_parent ON comments(parent_id);

-- Add favorite_count to assets
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'assets' AND column_name = 'favorite_count'
  ) THEN
    ALTER TABLE assets ADD COLUMN favorite_count INTEGER DEFAULT 0;
  END IF;
END $$;

-- Add comment_count to assets
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'assets' AND column_name = 'comment_count'
  ) THEN
    ALTER TABLE assets ADD COLUMN comment_count INTEGER DEFAULT 0;
  END IF;
END $$;

-- ============================================
-- CURATED COLLECTIONS (Staff picks, themed lists)
-- ============================================

CREATE TABLE IF NOT EXISTS curated_collections (
  id SERIAL PRIMARY KEY,
  collection_id UUID UNIQUE NOT NULL DEFAULT gen_random_uuid(),
  name VARCHAR(200) NOT NULL,
  slug VARCHAR(100) UNIQUE NOT NULL,
  description TEXT,
  cover_image TEXT,  -- Cover image URL
  curator_id INTEGER REFERENCES users(id) ON DELETE SET NULL,

  -- Display options
  is_featured BOOLEAN DEFAULT false,  -- Show on homepage
  is_staff_pick BOOLEAN DEFAULT false,
  is_public BOOLEAN DEFAULT true,
  position INTEGER DEFAULT 0,  -- Order in featured list

  -- Stats
  asset_count INTEGER DEFAULT 0,
  view_count INTEGER DEFAULT 0,

  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_curated_collections_slug ON curated_collections(slug);
CREATE INDEX IF NOT EXISTS idx_curated_collections_featured ON curated_collections(is_featured) WHERE is_featured = true;
CREATE INDEX IF NOT EXISTS idx_curated_collections_staff ON curated_collections(is_staff_pick) WHERE is_staff_pick = true;

-- Assets in curated collections
CREATE TABLE IF NOT EXISTS curated_collection_assets (
  id SERIAL PRIMARY KEY,
  collection_id INTEGER NOT NULL REFERENCES curated_collections(id) ON DELETE CASCADE,
  asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  position INTEGER DEFAULT 0,
  curator_note TEXT,  -- Why this asset was included
  added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  added_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  UNIQUE(collection_id, asset_id)
);

CREATE INDEX IF NOT EXISTS idx_curated_collection_assets_collection ON curated_collection_assets(collection_id);
CREATE INDEX IF NOT EXISTS idx_curated_collection_assets_asset ON curated_collection_assets(asset_id);

-- ============================================
-- IMPROVED SEARCH (Weighted full-text search)
-- ============================================

-- Add search_vector column for weighted search
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'assets' AND column_name = 'search_vector'
  ) THEN
    ALTER TABLE assets ADD COLUMN search_vector tsvector;
  END IF;
END $$;

-- Create GIN index for fast text search
CREATE INDEX IF NOT EXISTS idx_assets_search_vector ON assets USING GIN(search_vector);

-- Function to update search vector with weights
-- A = highest (name), B = high (tags), C = medium (description), D = low (readme)
CREATE OR REPLACE FUNCTION update_asset_search_vector()
RETURNS TRIGGER AS $$
BEGIN
  NEW.search_vector :=
    setweight(to_tsvector('english', coalesce(NEW.name, '')), 'A') ||
    setweight(to_tsvector('english', coalesce(array_to_string(NEW.tags, ' '), '')), 'B') ||
    setweight(to_tsvector('english', coalesce(NEW.description, '')), 'C') ||
    setweight(to_tsvector('english', coalesce(NEW.readme, '')), 'D');
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger to auto-update search vector
DROP TRIGGER IF EXISTS trig_assets_search_update ON assets;
CREATE TRIGGER trig_assets_search_update
  BEFORE INSERT OR UPDATE ON assets
  FOR EACH ROW EXECUTE FUNCTION update_asset_search_vector();

-- Update existing assets' search vectors
-- (This runs on schema init, safe to re-run)
UPDATE assets SET search_vector =
  setweight(to_tsvector('english', coalesce(name, '')), 'A') ||
  setweight(to_tsvector('english', coalesce(array_to_string(tags, ' '), '')), 'B') ||
  setweight(to_tsvector('english', coalesce(description, '')), 'C') ||
  setweight(to_tsvector('english', coalesce(readme, '')), 'D')
WHERE search_vector IS NULL;

-- Update curated collection counts function
CREATE OR REPLACE FUNCTION update_collection_counts()
RETURNS void AS $$
BEGIN
  UPDATE curated_collections cc SET asset_count = (
    SELECT COUNT(*)
    FROM curated_collection_assets cca
    JOIN assets a ON cca.asset_id = a.id
    WHERE cca.collection_id = cc.id AND a.is_public = true
  );
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- USER FOLDERS (Personal organization)
-- ============================================

CREATE TABLE IF NOT EXISTS user_folders (
  id SERIAL PRIMARY KEY,
  folder_id UUID UNIQUE NOT NULL DEFAULT gen_random_uuid(),
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name VARCHAR(100) NOT NULL,
  slug VARCHAR(100) NOT NULL,
  description TEXT,
  color VARCHAR(7),  -- Hex color for UI
  icon VARCHAR(50),  -- Icon name
  parent_id INTEGER REFERENCES user_folders(id) ON DELETE CASCADE,
  position INTEGER DEFAULT 0,

  -- Stats
  asset_count INTEGER DEFAULT 0,

  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

  UNIQUE(user_id, slug),
  UNIQUE(user_id, parent_id, name)  -- No duplicate names in same parent
);

CREATE INDEX IF NOT EXISTS idx_user_folders_user ON user_folders(user_id);
CREATE INDEX IF NOT EXISTS idx_user_folders_parent ON user_folders(parent_id);

-- Add folder_id to assets
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'assets' AND column_name = 'folder_id'
  ) THEN
    ALTER TABLE assets ADD COLUMN folder_id INTEGER REFERENCES user_folders(id) ON DELETE SET NULL;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_assets_folder ON assets(folder_id);

-- Add visibility enum to assets (replace is_public boolean)
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'assets' AND column_name = 'visibility'
  ) THEN
    ALTER TABLE assets ADD COLUMN visibility VARCHAR(20) DEFAULT 'public'
      CHECK (visibility IN ('public', 'unlisted', 'private', 'draft'));
    -- Migrate existing data
    UPDATE assets SET visibility = CASE WHEN is_public THEN 'public' ELSE 'private' END;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_assets_visibility ON assets(visibility);

-- Add media column to asset_drafts if it doesn't exist
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'asset_drafts' AND column_name = 'media'
  ) THEN
    ALTER TABLE asset_drafts ADD COLUMN media JSONB DEFAULT '[]';
  END IF;
END $$;

-- Add visibility column to asset_drafts
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'asset_drafts' AND column_name = 'visibility'
  ) THEN
    ALTER TABLE asset_drafts ADD COLUMN visibility VARCHAR(20) DEFAULT 'public'
      CHECK (visibility IN ('public', 'unlisted', 'private'));
  END IF;
END $$;

-- ============================================
-- MODERATION: User Roles & Status
-- ============================================

-- Add role column to users (owner > admin > moderator > user)
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'users' AND column_name = 'role'
  ) THEN
    ALTER TABLE users ADD COLUMN role VARCHAR(20) DEFAULT 'user'
      CHECK (role IN ('owner', 'admin', 'moderator', 'user'));
    -- Migrate existing admins
    UPDATE users SET role = 'admin' WHERE is_admin = true;
  END IF;
END $$;

-- Add moderation status to users
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'users' AND column_name = 'status'
  ) THEN
    ALTER TABLE users ADD COLUMN status VARCHAR(20) DEFAULT 'active'
      CHECK (status IN ('active', 'warned', 'suspended', 'banned'));
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'users' AND column_name = 'strike_count'
  ) THEN
    ALTER TABLE users ADD COLUMN strike_count INTEGER DEFAULT 0;
  END IF;

  -- Ensure all existing users have default values
  UPDATE users SET status = 'active' WHERE status IS NULL;
  UPDATE users SET role = 'user' WHERE role IS NULL;
  UPDATE users SET strike_count = 0 WHERE strike_count IS NULL;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'users' AND column_name = 'suspended_until'
  ) THEN
    ALTER TABLE users ADD COLUMN suspended_until TIMESTAMPTZ;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'users' AND column_name = 'ban_reason'
  ) THEN
    ALTER TABLE users ADD COLUMN ban_reason TEXT;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);

-- ============================================
-- USER PROFILE: Social Links & Credibility
-- ============================================

-- Add company/employer info
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'users' AND column_name = 'company'
  ) THEN
    ALTER TABLE users ADD COLUMN company VARCHAR(100);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'users' AND column_name = 'job_title'
  ) THEN
    ALTER TABLE users ADD COLUMN job_title VARCHAR(100);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'users' AND column_name = 'location'
  ) THEN
    ALTER TABLE users ADD COLUMN location VARCHAR(100);
  END IF;

  -- Social links stored as JSONB for flexibility
  -- Format: {"twitter": "handle", "linkedin": "url", "github": "username", ...}
  -- Supported: twitter, linkedin, github, artstation, instagram, youtube, vimeo, imdb, custom
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'users' AND column_name = 'social_links'
  ) THEN
    ALTER TABLE users ADD COLUMN social_links JSONB DEFAULT '{}';
  END IF;
END $$;

-- Add moderation status to assets
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'assets' AND column_name = 'status'
  ) THEN
    ALTER TABLE assets ADD COLUMN status VARCHAR(20) DEFAULT 'published'
      CHECK (status IN ('published', 'hidden', 'under_review', 'removed'));
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'assets' AND column_name = 'removed_reason'
  ) THEN
    ALTER TABLE assets ADD COLUMN removed_reason TEXT;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'assets' AND column_name = 'removed_by'
  ) THEN
    ALTER TABLE assets ADD COLUMN removed_by INTEGER REFERENCES users(id);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'assets' AND column_name = 'removed_at'
  ) THEN
    ALTER TABLE assets ADD COLUMN removed_at TIMESTAMPTZ;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_assets_status ON assets(status);

-- ============================================
-- MODERATION: Mod Actions Log
-- ============================================

CREATE TABLE IF NOT EXISTS mod_actions (
  id SERIAL PRIMARY KEY,
  moderator_id INTEGER NOT NULL REFERENCES users(id),
  action_type VARCHAR(50) NOT NULL,  -- hide_asset, remove_asset, restore_asset, warn_user, suspend_user, ban_user, unban_user, dismiss_report
  target_type VARCHAR(20) NOT NULL,  -- asset, user, report
  target_id INTEGER NOT NULL,
  reason TEXT NOT NULL,
  notes TEXT,  -- Internal notes for mod team
  metadata JSONB DEFAULT '{}',  -- Additional context (e.g., report_id, old_status)
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mod_actions_moderator ON mod_actions(moderator_id);
CREATE INDEX IF NOT EXISTS idx_mod_actions_target ON mod_actions(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_mod_actions_type ON mod_actions(action_type);
CREATE INDEX IF NOT EXISTS idx_mod_actions_created ON mod_actions(created_at);

-- Update folder asset counts function
CREATE OR REPLACE FUNCTION update_folder_counts()
RETURNS void AS $$
BEGIN
  UPDATE user_folders f SET asset_count = (
    SELECT COUNT(*)
    FROM assets a
    WHERE a.folder_id = f.id
  );
END;
$$ LANGUAGE plpgsql;

-- Trigger to update folder counts on asset changes
CREATE OR REPLACE FUNCTION update_folder_count_trigger()
RETURNS TRIGGER AS $$
BEGIN
  -- Update old folder count if folder changed
  IF TG_OP = 'UPDATE' AND OLD.folder_id IS DISTINCT FROM NEW.folder_id THEN
    IF OLD.folder_id IS NOT NULL THEN
      UPDATE user_folders SET asset_count = asset_count - 1 WHERE id = OLD.folder_id;
    END IF;
    IF NEW.folder_id IS NOT NULL THEN
      UPDATE user_folders SET asset_count = asset_count + 1 WHERE id = NEW.folder_id;
    END IF;
  ELSIF TG_OP = 'INSERT' AND NEW.folder_id IS NOT NULL THEN
    UPDATE user_folders SET asset_count = asset_count + 1 WHERE id = NEW.folder_id;
  ELSIF TG_OP = 'DELETE' AND OLD.folder_id IS NOT NULL THEN
    UPDATE user_folders SET asset_count = asset_count - 1 WHERE id = OLD.folder_id;
  END IF;

  RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trig_asset_folder_count ON assets;
CREATE TRIGGER trig_asset_folder_count
  AFTER INSERT OR UPDATE OR DELETE ON assets
  FOR EACH ROW EXECUTE FUNCTION update_folder_count_trigger();

-- ============================================
-- FEEDBACK (Bug reports & feature requests)
-- ============================================

CREATE TABLE IF NOT EXISTS feedback (
  id SERIAL PRIMARY KEY,
  type VARCHAR(20) NOT NULL CHECK (type IN ('bug', 'feature', 'other')),
  title VARCHAR(200) NOT NULL,
  description TEXT,
  email VARCHAR(255),
  user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  user_agent TEXT,
  status VARCHAR(20) DEFAULT 'new' CHECK (status IN ('new', 'reviewing', 'planned', 'in-progress', 'done', 'wont-fix')),
  admin_notes TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_feedback_status ON feedback(status);
CREATE INDEX IF NOT EXISTS idx_feedback_type ON feedback(type);
CREATE INDEX IF NOT EXISTS idx_feedback_user ON feedback(user_id);
CREATE INDEX IF NOT EXISTS idx_feedback_created ON feedback(created_at);

-- ============================================
-- TAG GROUPS (Curated tag organization for browsing)
-- ============================================

CREATE TABLE IF NOT EXISTS tag_groups (
  id SERIAL PRIMARY KEY,
  name VARCHAR(100) NOT NULL,           -- "Geometry"
  slug VARCHAR(100) UNIQUE NOT NULL,    -- "geometry"
  description TEXT,                      -- "Modeling and mesh operations"
  icon VARCHAR(50),                      -- Icon name for UI
  position INTEGER DEFAULT 0,            -- Display order
  tags TEXT[] NOT NULL DEFAULT '{}',     -- ['scatter', 'boolean', 'mesh']
  contexts TEXT[] DEFAULT '{}',          -- ['sop', 'vop'] - relevant contexts (empty = all)
  is_featured BOOLEAN DEFAULT true,      -- Show on browse page
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tag_groups_slug ON tag_groups(slug);
CREATE INDEX IF NOT EXISTS idx_tag_groups_position ON tag_groups(position);
CREATE INDEX IF NOT EXISTS idx_tag_groups_featured ON tag_groups(is_featured) WHERE is_featured = true;

-- Seed initial tag groups
INSERT INTO tag_groups (name, slug, description, icon, position, tags, contexts) VALUES
  ('Geometry', 'geometry', 'Modeling, mesh operations, and procedural geometry', 'box', 1,
   ARRAY['scatter', 'boolean', 'mesh', 'curves', 'procedural', 'points', 'topology', 'uv', 'normals', 'subdivision'],
   ARRAY['sop']),
  ('Effects', 'effects', 'Particles, simulations, and destruction', 'sparkles', 2,
   ARRAY['particles', 'pyro', 'fire', 'smoke', 'fluids', 'flip', 'destruction', 'rbd', 'vellum', 'cloth'],
   ARRAY['sop', 'dop']),
  ('Terrain', 'terrain', 'Heightfields, erosion, and landscape generation', 'mountain', 3,
   ARRAY['terrain', 'heightfield', 'erosion', 'landscape', 'biome', 'foliage', 'scatter'],
   ARRAY['sop']),
  ('Shading', 'shading', 'Materials, textures, and procedural patterns', 'palette', 4,
   ARRAY['material', 'shader', 'texture', 'pattern', 'noise', 'pbr', 'displacement'],
   ARRAY['vop', 'shop', 'lop']),
  ('USD & Layout', 'usd-layout', 'USD workflows, Solaris, and scene layout', 'layers', 5,
   ARRAY['usd', 'solaris', 'layout', 'instancing', 'lod', 'variant', 'composition'],
   ARRAY['lop']),
  ('Pipeline', 'pipeline', 'Workflow tools, automation, and utilities', 'wrench', 6,
   ARRAY['utility', 'debug', 'pipeline', 'automation', 'batch', 'export', 'import', 'cache'],
   ARRAY[]::TEXT[]),
  ('Animation', 'animation', 'Rigging, motion, and deformers', 'film', 7,
   ARRAY['rig', 'animation', 'deform', 'blend', 'motion', 'keyframe', 'constraint'],
   ARRAY['sop', 'obj', 'chop'])
ON CONFLICT (slug) DO UPDATE SET
  tags = EXCLUDED.tags,
  description = EXCLUDED.description,
  contexts = EXCLUDED.contexts,
  updated_at = NOW();

-- ============================================
-- SAVED ASSETS (User's library of copied/downloaded assets)
-- ============================================

CREATE TABLE IF NOT EXISTS saved_assets (
  id SERIAL PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  version_id INTEGER REFERENCES versions(id) ON DELETE SET NULL,  -- Which version was saved
  saved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  source VARCHAR(20) DEFAULT 'copy' CHECK (source IN ('copy', 'download', 'purchase', 'manual')),
  notes TEXT,                     -- User's personal notes
  folder VARCHAR(100),            -- Optional organization
  UNIQUE(user_id, asset_id)
);

CREATE INDEX IF NOT EXISTS idx_saved_assets_user ON saved_assets(user_id);
CREATE INDEX IF NOT EXISTS idx_saved_assets_asset ON saved_assets(asset_id);
CREATE INDEX IF NOT EXISTS idx_saved_assets_saved_at ON saved_assets(saved_at);

-- ============================================
-- TEAMS (Shared libraries for studios/groups)
-- ============================================

CREATE TABLE IF NOT EXISTS teams (
  id SERIAL PRIMARY KEY,
  team_id UUID UNIQUE NOT NULL DEFAULT gen_random_uuid(),
  name VARCHAR(100) NOT NULL,
  slug VARCHAR(50) UNIQUE NOT NULL,
  description TEXT,
  owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,

  -- Settings
  is_public BOOLEAN DEFAULT false,  -- Can anyone view team assets?
  invite_only BOOLEAN DEFAULT true,  -- Require invite to join?

  -- Stats
  member_count INTEGER DEFAULT 1,
  asset_count INTEGER DEFAULT 0,

  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_teams_slug ON teams(slug);
CREATE INDEX IF NOT EXISTS idx_teams_owner ON teams(owner_id);

-- Team membership
CREATE TABLE IF NOT EXISTS team_members (
  id SERIAL PRIMARY KEY,
  team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role VARCHAR(20) DEFAULT 'member' CHECK (role IN ('owner', 'admin', 'member')),
  joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  invited_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  UNIQUE(team_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_team_members_team ON team_members(team_id);
CREATE INDEX IF NOT EXISTS idx_team_members_user ON team_members(user_id);

-- Team's shared asset library
CREATE TABLE IF NOT EXISTS team_saved_assets (
  id SERIAL PRIMARY KEY,
  team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  version_id INTEGER REFERENCES versions(id) ON DELETE SET NULL,
  added_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  notes TEXT,
  folder VARCHAR(100),  -- Organization within team library
  UNIQUE(team_id, asset_id)
);

CREATE INDEX IF NOT EXISTS idx_team_saved_assets_team ON team_saved_assets(team_id);
CREATE INDEX IF NOT EXISTS idx_team_saved_assets_asset ON team_saved_assets(asset_id);

-- Team invites (pending invitations)
CREATE TABLE IF NOT EXISTS team_invites (
  id SERIAL PRIMARY KEY,
  team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  email VARCHAR(255) NOT NULL,
  invite_code VARCHAR(64) UNIQUE NOT NULL,
  role VARCHAR(20) DEFAULT 'member',
  invited_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  expires_at TIMESTAMP NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(team_id, email)
);

CREATE INDEX IF NOT EXISTS idx_team_invites_code ON team_invites(invite_code);
CREATE INDEX IF NOT EXISTS idx_team_invites_email ON team_invites(email);

-- ============================================
-- FORKS (Derived assets from community)
-- ============================================

-- Add fork columns to assets
DO $$
BEGIN
  -- Reference to the original asset this was forked from
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'assets' AND column_name = 'forked_from_id'
  ) THEN
    ALTER TABLE assets ADD COLUMN forked_from_id INTEGER REFERENCES assets(id) ON DELETE SET NULL;
  END IF;

  -- Count of how many times this asset has been forked
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'assets' AND column_name = 'fork_count'
  ) THEN
    ALTER TABLE assets ADD COLUMN fork_count INTEGER DEFAULT 0;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_assets_forked_from ON assets(forked_from_id);

-- License compatibility rules for forking
-- Defines which licenses can be used when forking from a given license
CREATE TABLE IF NOT EXISTS license_fork_rules (
  id SERIAL PRIMARY KEY,
  source_license VARCHAR(50) NOT NULL,       -- Original asset's license
  allowed_license VARCHAR(50) NOT NULL,      -- Licenses allowed for forks
  requires_attribution BOOLEAN DEFAULT true, -- Must credit original
  requires_same_license BOOLEAN DEFAULT false, -- Copyleft (must use same license)
  UNIQUE(source_license, allowed_license)
);

-- Seed license fork rules
INSERT INTO license_fork_rules (source_license, allowed_license, requires_attribution, requires_same_license)
VALUES
  -- MIT: Very permissive, can fork to almost anything
  ('mit', 'mit', true, false),
  ('mit', 'apache-2.0', true, false),
  ('mit', 'gpl-3.0', true, false),
  ('mit', 'bsd-3-clause', true, false),
  ('mit', 'cc-by-4.0', true, false),
  ('mit', 'cc-by-sa-4.0', true, false),

  -- Apache 2.0: Similar to MIT
  ('apache-2.0', 'apache-2.0', true, false),
  ('apache-2.0', 'gpl-3.0', true, false),
  ('apache-2.0', 'mit', true, false),

  -- GPL-3.0: Copyleft - must stay GPL
  ('gpl-3.0', 'gpl-3.0', true, true),

  -- BSD-3-Clause: Permissive like MIT
  ('bsd-3-clause', 'bsd-3-clause', true, false),
  ('bsd-3-clause', 'mit', true, false),
  ('bsd-3-clause', 'apache-2.0', true, false),
  ('bsd-3-clause', 'gpl-3.0', true, false),

  -- CC-BY-4.0: Attribution required
  ('cc-by-4.0', 'cc-by-4.0', true, false),
  ('cc-by-4.0', 'cc-by-sa-4.0', true, false),

  -- CC-BY-SA-4.0: ShareAlike - must stay CC-BY-SA
  ('cc-by-sa-4.0', 'cc-by-sa-4.0', true, true),

  -- CC0/Unlicense: Public domain, no restrictions
  ('cc0-1.0', 'cc0-1.0', false, false),
  ('cc0-1.0', 'mit', false, false),
  ('cc0-1.0', 'apache-2.0', false, false),
  ('cc0-1.0', 'gpl-3.0', false, false),
  ('cc0-1.0', 'cc-by-4.0', false, false),
  ('unlicense', 'unlicense', false, false),
  ('unlicense', 'mit', false, false),
  ('unlicense', 'apache-2.0', false, false),
  ('unlicense', 'gpl-3.0', false, false),

  -- CC0 (short form used by web UI)
  ('cc0', 'cc0', false, false),
  ('cc0', 'mit', false, false),
  ('cc0', 'apache-2.0', false, false),
  ('cc0', 'gpl-3.0', false, false),
  ('cc0', 'cc-by-4.0', false, false)

  -- Note: 'proprietary' is NOT in this table - cannot be forked
ON CONFLICT (source_license, allowed_license) DO NOTHING;

-- ============================================
-- MIGRATION: Add 'vex' asset type support
-- ============================================
DO $$
BEGIN
  -- Update asset_type CHECK constraint to include 'vex'
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'assets_asset_type_check'
      AND pg_get_constraintdef(oid) LIKE '%vex%'
  ) THEN
    ALTER TABLE assets DROP CONSTRAINT IF EXISTS assets_asset_type_check;
    ALTER TABLE assets ADD CONSTRAINT assets_asset_type_check
      CHECK (asset_type IN ('node', 'hda', 'vex', 'collection'));
  END IF;

  -- Update houdini_context CHECK constraint to include 'vex'
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'assets_houdini_context_check'
      AND pg_get_constraintdef(oid) LIKE '%vex%'
  ) THEN
    ALTER TABLE assets DROP CONSTRAINT IF EXISTS assets_houdini_context_check;
    ALTER TABLE assets ADD CONSTRAINT assets_houdini_context_check
      CHECK (houdini_context IN ('sop', 'vop', 'lop', 'obj', 'cop', 'top', 'chop', 'dop', 'shop', 'rop', 'vex'));
  END IF;
END $$;

-- ============================================
-- MIGRATION: Team-owned assets (on-prem team library)
-- ============================================
-- A non-NULL team_id makes the asset team-scoped: only members of that team
-- can read or write it via /api/v1/teams/:slug/library/*. NULL preserves
-- the existing per-user / public registry behavior.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'assets' AND column_name = 'team_id'
  ) THEN
    ALTER TABLE assets ADD COLUMN team_id INTEGER REFERENCES teams(id) ON DELETE CASCADE;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'user_folders' AND column_name = 'team_id'
  ) THEN
    ALTER TABLE user_folders ADD COLUMN team_id INTEGER REFERENCES teams(id) ON DELETE CASCADE;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_assets_team ON assets(team_id) WHERE team_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_user_folders_team ON user_folders(team_id) WHERE team_id IS NOT NULL;

-- Houdini icon name (e.g. 'SOP_scatter') for display in the panel and
-- TAB menu. Per-asset, set at publish time. NAS migrations carry it
-- over from the legacy library_assets.icon column; non-migrated
-- packages can leave it null.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'assets' AND column_name = 'icon'
  ) THEN
    ALTER TABLE assets ADD COLUMN icon VARCHAR(64);
  END IF;
END $$;

-- Replace the strict UNIQUE(owner_id, slug) constraint with a partial
-- unique index that only enforces uniqueness among LIVE assets. Without
-- this, soft-deleted (is_deprecated=true) rows keep the slug locked,
-- and a user who deletes an asset then tries to publish a new one with
-- the same name hits a duplicate-key error from the DB.
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'assets_owner_id_slug_key'
  ) THEN
    ALTER TABLE assets DROP CONSTRAINT assets_owner_id_slug_key;
  END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS idx_assets_owner_slug_live
  ON assets(owner_id, slug)
  WHERE COALESCE(is_deprecated, false) = false;

-- Team-scoped temporary shares. The Houdini panel's "Quick Copy"
-- creates a temp share on the on-prem server; workstation B (same
-- team) reads /teams/:slug/share/latest to find the most-recent
-- non-expired team share without having to type the share code.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'temp_shares' AND column_name = 'team_id'
  ) THEN
    ALTER TABLE temp_shares ADD COLUMN team_id INTEGER REFERENCES teams(id) ON DELETE CASCADE;
  END IF;
END $$;

-- Fast "latest non-expired team share" lookups.
CREATE INDEX IF NOT EXISTS idx_temp_shares_team_latest
  ON temp_shares(team_id, created_at DESC)
  WHERE team_id IS NOT NULL;
