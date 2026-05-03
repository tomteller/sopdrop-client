/**
 * Authentication middleware
 */

import jwt from 'jsonwebtoken';
import crypto from 'crypto';
import { query } from '../models/db.js';
import { AuthError, ForbiddenError } from './errorHandler.js';
import { isReservedUsername } from './security.js';

// JWT_SECRET is validated at startup by security.js - no default allowed
const JWT_SECRET = process.env.JWT_SECRET;

/**
 * Hash an API token for storage
 */
export function hashToken(token) {
  return crypto.createHash('sha256').update(token).digest('hex');
}

/**
 * Generate a new API token
 */
export function generateToken() {
  return `sdrop_${crypto.randomBytes(32).toString('hex')}`;
}

/**
 * Verify JWT token
 */
export function verifyJWT(token) {
  try {
    return jwt.verify(token, JWT_SECRET);
  } catch (error) {
    return null;
  }
}

/**
 * Generate JWT token
 */
export function signJWT(payload, expiresIn = '30d') {
  return jwt.sign(payload, JWT_SECRET, { expiresIn });
}

// Trust-LAN auth: when TRUST_LAN_AUTH=true, requests with no Bearer token
// are accepted if they carry an X-Sopdrop-User header. The user is auto-
// created on first sight (no password, no token). Intended for on-prem
// deployments where the LAN itself is the trust boundary — never enable
// this on a server reachable from the public internet.
const TRUST_LAN_AUTH = process.env.TRUST_LAN_AUTH === 'true';

// Optional default team for trust-LAN auth. When set, every trust-LAN
// request also ensures membership in this team (idempotent upsert).
// Avoids the per-artist manual `INSERT INTO team_members` chore on a
// trusted internal LAN where everyone belongs to the same team.
// Use the team's slug, e.g. TRUST_LAN_DEFAULT_TEAM=frame48.
const TRUST_LAN_DEFAULT_TEAM = (process.env.TRUST_LAN_DEFAULT_TEAM || '').trim().toLowerCase() || null;

// Cache the team's id so we don't re-resolve the slug on every request.
// Invalidated by process restart, which is fine — TRUST_LAN_DEFAULT_TEAM
// is set in env, so the team had better exist when the server starts.
let _trustLanDefaultTeamId = undefined;
let _trustLanDefaultTeamWarned = false;

async function getTrustLanDefaultTeamId() {
  if (!TRUST_LAN_DEFAULT_TEAM) return null;
  if (_trustLanDefaultTeamId !== undefined) return _trustLanDefaultTeamId;
  try {
    const result = await query(
      'SELECT id FROM teams WHERE slug = $1',
      [TRUST_LAN_DEFAULT_TEAM]
    );
    if (result.rows.length === 0) {
      if (!_trustLanDefaultTeamWarned) {
        console.warn(
          `[auth] TRUST_LAN_DEFAULT_TEAM='${TRUST_LAN_DEFAULT_TEAM}' but no team with that slug exists. ` +
          `Create it (e.g. via deploy/onprem/scripts/create-team.sh) and restart the server.`
        );
        _trustLanDefaultTeamWarned = true;
      }
      _trustLanDefaultTeamId = null;
      return null;
    }
    _trustLanDefaultTeamId = result.rows[0].id;
    return _trustLanDefaultTeamId;
  } catch (e) {
    console.warn(`[auth] Failed to resolve TRUST_LAN_DEFAULT_TEAM: ${e.message}`);
    return null;
  }
}

async function ensureTrustLanDefaultMembership(userId) {
  const teamId = await getTrustLanDefaultTeamId();
  if (!teamId) return;
  try {
    // ON CONFLICT DO NOTHING — idempotent for repeat requests AND covers
    // existing users who pre-date this feature (no manual backfill needed).
    // Updates teams.member_count only when a new row was actually inserted.
    const result = await query(`
      INSERT INTO team_members (team_id, user_id, role)
      VALUES ($1, $2, 'member')
      ON CONFLICT (team_id, user_id) DO NOTHING
      RETURNING id
    `, [teamId, userId]);
    if (result.rowCount > 0) {
      await query(
        'UPDATE teams SET member_count = member_count + 1 WHERE id = $1',
        [teamId]
      );
    }
  } catch (e) {
    // Non-fatal: log and continue. Auth shouldn't fail because team
    // bookkeeping had a hiccup.
    console.warn(`[auth] Failed to ensure trust-LAN default team membership for user ${userId}: ${e.message}`);
  }
}

/**
 * Sanitize a workstation-supplied username. Lowercase, alphanumeric +
 * dash + underscore + dot, max 32 chars. Returns null if the input is
 * unusable (empty, all symbols, reserved name).
 */
export function sanitizeLanUsername(raw) {
  if (!raw || typeof raw !== 'string') return null;
  const cleaned = raw
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9._-]/g, '')
    .slice(0, 32);
  if (!cleaned || cleaned.length < 2) return null;
  if (isReservedUsername(cleaned)) return null;
  return cleaned;
}

/**
 * Look up (or create) a user by workstation-supplied username.
 * Used only when TRUST_LAN_AUTH is on and no Bearer token was provided.
 */
async function findOrCreateLanUser(username) {
  return findOrCreateUserByUsername(username);
}

/**
 * Look up a user by username, creating a placeholder record if missing.
 * Used by trust-LAN auth and by admin-only migration flows that need to
 * preserve original authorship from a legacy library. Same shape as the
 * trust-LAN auto-create: synthesized `<name>@lan.local` email, sentinel
 * password hash, role='user', email_verified=true. Username is expected
 * to already be sanitized by the caller.
 */
export async function findOrCreateUserByUsername(username) {
  if (!username) throw new Error('username required');
  const existing = await query(
    'SELECT id, username, email, is_admin, role, status, suspended_until FROM users WHERE username = $1',
    [username]
  );
  if (existing.rows.length > 0) return existing.rows[0];

  const fakeEmail = `${username}@lan.local`;
  try {
    const created = await query(`
      INSERT INTO users (username, email, password_hash, role, status, email_verified)
      VALUES ($1, $2, '__lan_trust_no_password__', 'user', 'active', true)
      RETURNING id, username, email, is_admin, role, status, suspended_until
    `, [username, fakeEmail]);
    return created.rows[0];
  } catch (e) {
    // Race: another concurrent request just created the same user. Re-fetch.
    if (e.code === '23505') {
      const reFetch = await query(
        'SELECT id, username, email, is_admin, role, status, suspended_until FROM users WHERE username = $1',
        [username]
      );
      if (reFetch.rows.length > 0) return reFetch.rows[0];
    }
    throw e;
  }
}

/**
 * Authentication middleware
 *
 * Supports both JWT tokens and API tokens (sdrop_xxx format).
 * When TRUST_LAN_AUTH=true, also accepts requests with no Bearer token
 * if they carry an X-Sopdrop-User header (workstation OS username).
 */
export async function authenticate(req, res, next) {
  const authHeader = req.headers.authorization;

  // Trust-LAN fallback when no Bearer token is present.
  if (!authHeader && TRUST_LAN_AUTH) {
    const username = sanitizeLanUsername(req.headers['x-sopdrop-user']);
    if (!username) {
      return next(new AuthError(
        'Trust-LAN mode is on but X-Sopdrop-User header is missing or invalid'
      ));
    }
    try {
      const user = await findOrCreateLanUser(username);
      if (user.status === 'banned') {
        return next(new ForbiddenError('Account has been banned'));
      }
      // Auto-add to the configured default team (no-op if env unset or
      // user is already a member). Covers both new users on first sight
      // and pre-existing users who haven't been added yet.
      await ensureTrustLanDefaultMembership(user.id);
      req.user = {
        id: user.id,
        username: user.username,
        email: user.email,
        isAdmin: user.is_admin,
        role: user.role || 'user',
        status: user.status || 'active',
        scopes: ['read', 'write'],
        authType: 'lan_trust',
      };
      return next();
    } catch (err) {
      return next(new AuthError(`Trust-LAN auth failed: ${err.message}`));
    }
  }

  if (!authHeader) {
    return next(new AuthError('Authorization header required'));
  }

  const [type, token] = authHeader.split(' ');

  if (type !== 'Bearer' || !token) {
    return next(new AuthError('Invalid authorization format. Use: Bearer <token>'));
  }

  try {
    // Check if it's an API token (starts with sdrop_)
    if (token.startsWith('sdrop_')) {
      const tokenHash = hashToken(token);

      const result = await query(`
        SELECT
          t.id as token_id,
          t.scopes,
          t.expires_at,
          u.id as user_id,
          u.username,
          u.email,
          u.is_admin,
          u.role,
          u.status,
          u.suspended_until
        FROM api_tokens t
        JOIN users u ON t.user_id = u.id
        WHERE t.token_hash = $1
      `, [tokenHash]);

      if (result.rows.length === 0) {
        return next(new AuthError('Invalid API token'));
      }

      const tokenData = result.rows[0];

      // Check expiration
      if (tokenData.expires_at && new Date(tokenData.expires_at) < new Date()) {
        return next(new AuthError('API token has expired'));
      }

      // Check user status
      if (tokenData.status === 'banned') {
        return next(new ForbiddenError('Account has been banned'));
      }

      if (tokenData.status === 'suspended') {
        if (tokenData.suspended_until && new Date(tokenData.suspended_until) > new Date()) {
          return next(new ForbiddenError(`Account suspended until ${tokenData.suspended_until}`));
        }
      }

      // Update last used
      await query(`
        UPDATE api_tokens SET last_used_at = NOW(), last_used_ip = $1
        WHERE id = $2
      `, [req.ip, tokenData.token_id]);

      // Set user on request
      req.user = {
        id: tokenData.user_id,
        username: tokenData.username,
        email: tokenData.email,
        isAdmin: tokenData.is_admin,
        role: tokenData.role || 'user',
        status: tokenData.status || 'active',
        scopes: tokenData.scopes,
        authType: 'api_token',
      };

      return next();
    }

    // Otherwise, treat as JWT
    const decoded = verifyJWT(token);

    if (!decoded) {
      return next(new AuthError('Invalid or expired token'));
    }

    // Get user from database
    const result = await query(`
      SELECT id, username, email, is_admin, role, status, suspended_until
      FROM users
      WHERE id = $1
    `, [decoded.sub || decoded.userId]);

    if (result.rows.length === 0) {
      return next(new AuthError('User not found'));
    }

    const user = result.rows[0];

    // Check user status
    if (user.status === 'banned') {
      return next(new ForbiddenError('Account has been banned'));
    }

    if (user.status === 'suspended') {
      if (user.suspended_until && new Date(user.suspended_until) > new Date()) {
        return next(new ForbiddenError(`Account suspended until ${user.suspended_until}`));
      }
    }

    req.user = {
      id: user.id,
      username: user.username,
      email: user.email,
      isAdmin: user.is_admin,
      role: user.role || 'user',
      status: user.status || 'active',
      authType: 'jwt',
    };

    next();
  } catch (error) {
    next(new AuthError('Authentication failed'));
  }
}

/**
 * Optional authentication - sets req.user if token provided, continues otherwise
 */
export async function optionalAuth(req, res, next) {
  const authHeader = req.headers.authorization;

  if (!authHeader) {
    req.user = null;
    return next();
  }

  // Use the regular authenticate, but catch errors
  authenticate(req, res, (err) => {
    if (err) {
      // If auth fails, just continue without user — but log for monitoring
      console.warn(`[optionalAuth] Token validation failed: ${err.message} (IP: ${req.ip}, path: ${req.originalUrl})`);
      req.user = null;
    }
    next();
  });
}

/**
 * Require admin role
 */
export function requireAdmin(req, res, next) {
  if (!req.user) {
    return next(new AuthError('Authentication required'));
  }

  if (!req.user.isAdmin) {
    return next(new ForbiddenError('Admin access required'));
  }

  next();
}

/**
 * Require specific scope (for API tokens)
 */
export function requireScope(scope) {
  return (req, res, next) => {
    if (!req.user) {
      return next(new AuthError('Authentication required'));
    }

    // JWT users have all scopes
    if (req.user.authType === 'jwt') {
      return next();
    }

    // Check API token scopes
    if (!req.user.scopes || !req.user.scopes.includes(scope)) {
      return next(new ForbiddenError(`Scope '${scope}' required`));
    }

    next();
  };
}

// Role hierarchy: owner > admin > moderator > user
const ROLE_HIERARCHY = {
  owner: 4,
  admin: 3,
  moderator: 2,
  user: 1,
};

/**
 * Check if user has at least the specified role level
 */
export function hasRole(userRole, requiredRole) {
  return (ROLE_HIERARCHY[userRole] || 1) >= (ROLE_HIERARCHY[requiredRole] || 1);
}

/**
 * Require a minimum role level
 * owner > admin > moderator > user
 */
export function requireRole(minimumRole) {
  return (req, res, next) => {
    if (!req.user) {
      return next(new AuthError('Authentication required'));
    }

    if (!hasRole(req.user.role, minimumRole)) {
      return next(new ForbiddenError(`${minimumRole} access required`));
    }

    next();
  };
}

/**
 * Require moderator role or higher
 */
export function requireMod(req, res, next) {
  if (!req.user) {
    return next(new AuthError('Authentication required'));
  }

  if (!hasRole(req.user.role, 'moderator')) {
    return next(new ForbiddenError('Moderator access required'));
  }

  next();
}

/**
 * Require owner role (only the site owner)
 */
export function requireOwner(req, res, next) {
  if (!req.user) {
    return next(new AuthError('Authentication required'));
  }

  if (req.user.role !== 'owner') {
    return next(new ForbiddenError('Owner access required'));
  }

  next();
}
