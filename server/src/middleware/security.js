/**
 * Security middleware
 * Handles security headers, rate limiting, request tracking, and audit logging
 */

import helmet from 'helmet';
import rateLimit from 'express-rate-limit';
import crypto from 'crypto';
import { query } from '../models/db.js';

// ============================================
// REQUEST ID TRACKING
// ============================================

/**
 * Add unique request ID for tracing
 */
export function requestId(req, res, next) {
  req.id = crypto.randomUUID();
  res.setHeader('X-Request-ID', req.id);
  next();
}

// ============================================
// SECURITY HEADERS (helmet.js)
// ============================================

export const securityHeaders = helmet({
  contentSecurityPolicy: {
    directives: {
      defaultSrc: ["'self'"],
      styleSrc: ["'self'", "'unsafe-inline'"],
      scriptSrc: ["'self'"],
      imgSrc: ["'self'", "data:", "https:"],
      connectSrc: ["'self'"],
      fontSrc: ["'self'"],
      objectSrc: ["'none'"],
      mediaSrc: ["'self'"],
      frameSrc: ["'none'"],
    },
  },
  crossOriginEmbedderPolicy: false, // Needed for loading external resources
  crossOriginResourcePolicy: { policy: "cross-origin" }, // Allow API access
  strictTransportSecurity: {
    maxAge: 31536000, // 1 year
    includeSubDomains: true,
    preload: true,
  },
  frameguard: { action: 'deny' },
});

// ============================================
// HTTPS REDIRECT (Production)
// ============================================

export function httpsRedirect(req, res, next) {
  if (process.env.NODE_ENV === 'production') {
    // Skip redirect for API routes — they come via Vercel proxy or direct HTTPS
    // Redirecting API POSTs (especially file uploads) breaks them
    if (req.url.startsWith('/api/')) return next();
    if (req.headers['x-forwarded-proto'] !== 'https') {
      return res.redirect(301, `https://${req.headers.host}${req.url}`);
    }
  }
  next();
}

// ============================================
// RATE LIMITING
// ============================================

// Standard rate limit message
const rateLimitMessage = {
  error: 'Too many requests, please try again later',
  code: 'RATE_LIMIT_EXCEEDED',
};

// Rate limit handler that logs the event
const rateLimitHandler = (req, res, options) => {
  // Log rate limit hit for security monitoring
  logSecurityEvent('rate_limit_exceeded', req, {
    endpoint: req.originalUrl,
    method: req.method,
    limit: options.max,
    windowMs: options.windowMs,
  });

  res.status(429).json(rateLimitMessage);
};

// Bypass rate limiting for owner/admin tokens. Bulk operations like
// the NAS migration script (--preserve-authorship) legitimately need
// to push hundreds of uploads in a row, and throttling them at user
// limits turns a 5-minute job into hours. Admins are already trusted
// by definition; if an admin token is compromised, rate limiting is
// not the layer that saves you.
//
// For pre-auth limiters (login/register/oauth) req.user is undefined
// so this is a no-op there — those still throttle by IP. A skip
// returning true means the limiter does NOT count or block this req.
const skipForAdmins = (req) =>
  req.user?.isAdmin === true || req.user?.role === 'owner' || req.user?.role === 'admin';

// On-prem deployments with TRUST_LAN_AUTH=true want zero rate limiting
// for traffic coming from authenticated workstations on the trusted
// LAN. The presence of X-Sopdrop-User is the signal: in trust-LAN mode
// the Houdini panel sets it on every request, and the server's auth
// middleware uses it to identify the caller. Skipping the global
// limiter here lets a busy team (5+ active artists browsing/pasting
// from the team library) work normally instead of hitting 429s on
// reads. Public-internet traffic (no header) is still throttled.
//
// Per-route limiters (uploadLimiter etc.) still apply via skipForAdmins
// — write paths get tighter scrutiny than reads.
const TRUST_LAN_AUTH = process.env.TRUST_LAN_AUTH === 'true';
const skipForTrustLan = (req) =>
  TRUST_LAN_AUTH && Boolean(req.headers['x-sopdrop-user']);

// All limit values are env-configurable so on-prem operators can tune
// for team size without forking. Defaults match the previous hard-coded
// values; setting MAX=0 disables the limiter entirely.
const intEnv = (name, fallback) => {
  const raw = process.env[name];
  if (raw === undefined || raw === '') return fallback;
  const n = parseInt(raw, 10);
  return Number.isFinite(n) && n >= 0 ? n : fallback;
};
const RATE_LOGIN_MAX = intEnv('RATE_LOGIN_MAX', 5);
const RATE_LOGIN_WINDOW_MS = intEnv('RATE_LOGIN_WINDOW_MS', 15 * 60 * 1000);
const RATE_REGISTER_MAX = intEnv('RATE_REGISTER_MAX', 3);
const RATE_REGISTER_WINDOW_MS = intEnv('RATE_REGISTER_WINDOW_MS', 60 * 60 * 1000);
const RATE_OAUTH_MAX = intEnv('RATE_OAUTH_MAX', 15);
const RATE_OAUTH_WINDOW_MS = intEnv('RATE_OAUTH_WINDOW_MS', 15 * 60 * 1000);
const RATE_TOKEN_MAX = intEnv('RATE_TOKEN_MAX', 5);
const RATE_TOKEN_WINDOW_MS = intEnv('RATE_TOKEN_WINDOW_MS', 60 * 60 * 1000);
const RATE_UPLOAD_MAX = intEnv('RATE_UPLOAD_MAX', 10);
const RATE_UPLOAD_WINDOW_MS = intEnv('RATE_UPLOAD_WINDOW_MS', 60 * 60 * 1000);
const RATE_VERSION_MAX = intEnv('RATE_VERSION_MAX', 20);
const RATE_VERSION_WINDOW_MS = intEnv('RATE_VERSION_WINDOW_MS', 60 * 60 * 1000);
const RATE_DOWNLOAD_MAX = intEnv('RATE_DOWNLOAD_MAX', 60);
const RATE_DOWNLOAD_WINDOW_MS = intEnv('RATE_DOWNLOAD_WINDOW_MS', 60 * 1000);
const RATE_SHARE_MAX = intEnv('RATE_SHARE_MAX', 10);
const RATE_SHARE_WINDOW_MS = intEnv('RATE_SHARE_WINDOW_MS', 60 * 60 * 1000);
const RATE_PASSWORD_CHANGE_MAX = intEnv('RATE_PASSWORD_CHANGE_MAX', 3);
const RATE_PASSWORD_CHANGE_WINDOW_MS = intEnv('RATE_PASSWORD_CHANGE_WINDOW_MS', 60 * 60 * 1000);
const RATE_GENERAL_MAX = intEnv('RATE_GENERAL_MAX', 100);
const RATE_GENERAL_WINDOW_MS = intEnv('RATE_GENERAL_WINDOW_MS', 60 * 1000);

// Pass-through middleware for limiters configured to MAX=0 (disabled).
// rateLimit({ max: 0 }) blocks all requests; we want the opposite —
// "0 means don't limit at all" — so we substitute a no-op middleware.
const noopMiddleware = (_req, _res, next) => next();

// Helper that builds a rateLimit middleware OR returns a no-op when
// the configured max is 0 (disabled by the operator).
const buildLimiter = ({ windowMs, max, keyGenerator, skip }) =>
  max === 0
    ? noopMiddleware
    : rateLimit({
        windowMs,
        max,
        message: rateLimitMessage,
        standardHeaders: true,
        legacyHeaders: false,
        handler: rateLimitHandler,
        keyGenerator,
        skip,
        validate: false,
      });

// Login: Strict limit to prevent brute force
export const loginLimiter = buildLimiter({
  windowMs: RATE_LOGIN_WINDOW_MS,
  max: RATE_LOGIN_MAX,
  keyGenerator: (req) => req.ip,
  skip: skipForAdmins,
});

// Registration: Prevent mass account creation
export const registerLimiter = buildLimiter({
  windowMs: RATE_REGISTER_WINDOW_MS,
  max: RATE_REGISTER_MAX,
  keyGenerator: (req) => req.ip,
  skip: skipForAdmins,
});

// OAuth: Handles both login and registration in one endpoint
export const oauthLimiter = buildLimiter({
  windowMs: RATE_OAUTH_WINDOW_MS,
  max: RATE_OAUTH_MAX,
  keyGenerator: (req) => req.ip,
  skip: skipForAdmins,
});

// Token generation: Prevent token farming
export const tokenLimiter = buildLimiter({
  windowMs: RATE_TOKEN_WINDOW_MS,
  max: RATE_TOKEN_MAX,
  keyGenerator: (req) => req.user?.id || req.ip,
  skip: skipForAdmins,
});

// Asset upload: Prevent storage abuse
export const uploadLimiter = buildLimiter({
  windowMs: RATE_UPLOAD_WINDOW_MS,
  max: RATE_UPLOAD_MAX,
  keyGenerator: (req) => req.user?.id || req.ip,
  skip: skipForAdmins,
});

// Version publish: Allow more than new assets
export const versionLimiter = buildLimiter({
  windowMs: RATE_VERSION_WINDOW_MS,
  max: RATE_VERSION_MAX,
  keyGenerator: (req) => req.user?.id || req.ip,
  skip: skipForAdmins,
});

// Downloads: Prevent scraping. Trust-LAN clients (Houdini panel
// browsing/pasting from the team library) skip this entirely —
// reads aren't an abuse vector on a trusted internal LAN.
export const downloadLimiter = buildLimiter({
  windowMs: RATE_DOWNLOAD_WINDOW_MS,
  max: RATE_DOWNLOAD_MAX,
  keyGenerator: (req) => req.ip,
  skip: (req) => skipForAdmins(req) || skipForTrustLan(req),
});

// Temporary shares: Prevent abuse
export const shareLimiter = buildLimiter({
  windowMs: RATE_SHARE_WINDOW_MS,
  max: RATE_SHARE_MAX,
  keyGenerator: (req) => req.user?.id || req.ip,
  skip: skipForAdmins,
});

// Password change: Prevent abuse after account compromise
export const passwordChangeLimiter = buildLimiter({
  windowMs: RATE_PASSWORD_CHANGE_WINDOW_MS,
  max: RATE_PASSWORD_CHANGE_MAX,
  keyGenerator: (req) => req.user?.id || req.ip,
  skip: skipForAdmins,
});

// General API: Fallback limit applied app-wide. Skipped for trust-LAN
// requests so a busy team (5+ artists pasting from the team library)
// doesn't hit 429s on reads. Public-internet traffic still throttled.
export const generalLimiter = buildLimiter({
  windowMs: RATE_GENERAL_WINDOW_MS,
  max: RATE_GENERAL_MAX,
  keyGenerator: (req) => req.ip,
  skip: (req) => skipForAdmins(req) || skipForTrustLan(req),
});

// ============================================
// AUDIT LOGGING
// ============================================

/**
 * Log an audit event to the database
 */
export async function logAuditEvent(eventType, category, req, details = {}) {
  try {
    await query(`
      INSERT INTO audit_logs (event_type, event_category, actor_id, actor_ip, target_type, target_id, details, request_id)
      VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
    `, [
      eventType,
      category,
      req.user?.id || null,
      req.ip,
      details.targetType || null,
      details.targetId || null,
      JSON.stringify(details),
      req.id || null,
    ]);
  } catch (error) {
    // Don't fail the request if logging fails, but log to console
    console.error('Audit log failed:', error.message);
  }
}

/**
 * Log a security event (convenience wrapper)
 */
export function logSecurityEvent(eventType, req, details = {}) {
  return logAuditEvent(eventType, 'SECURITY', req, details);
}

/**
 * Log an auth event
 */
export function logAuthEvent(eventType, req, details = {}) {
  return logAuditEvent(eventType, 'AUTH', req, details);
}

/**
 * Log an asset event
 */
export function logAssetEvent(eventType, req, details = {}) {
  return logAuditEvent(eventType, 'ASSET', req, details);
}

/**
 * Log an admin event
 */
export function logAdminEvent(eventType, req, details = {}) {
  return logAuditEvent(eventType, 'ADMIN', req, details);
}

// ============================================
// RESERVED USERNAMES
// ============================================

export const RESERVED_USERNAMES = [
  // System/admin
  'admin', 'administrator', 'root', 'system', 'sopdrop', 'moderator', 'mod',
  // Official entities
  'sidefx', 'houdini', 'official', 'verified', 'staff', 'team',
  // Support
  'support', 'help', 'contact', 'info', 'abuse', 'security',
  // Infrastructure
  'api', 'www', 'mail', 'ftp', 'cdn', 'assets', 'static', 'files',
  // Reserved paths
  'login', 'logout', 'register', 'signup', 'signin', 'auth', 'oauth',
  'settings', 'profile', 'account', 'dashboard', 'explore', 'search',
  // Programming/keywords
  'null', 'undefined', 'true', 'false', 'none', 'nil',
  // Test
  'test', 'demo', 'example', 'sample',
];

/**
 * Check if a username is reserved
 */
export function isReservedUsername(username) {
  return RESERVED_USERNAMES.includes(username.toLowerCase());
}

// ============================================
// SECRETS VALIDATION (called at startup)
// ============================================

/**
 * Validate that required secrets are set
 * Call this before starting the server
 */
export function validateSecrets() {
  const required = [
    { key: 'JWT_SECRET', minLength: 32 },
  ];

  // Only require DB_PASSWORD if DATABASE_URL is not set
  if (!process.env.DATABASE_URL) {
    required.push({ key: 'DB_PASSWORD', minLength: 1 });
  }

  const errors = [];

  for (const { key, minLength } of required) {
    const value = process.env[key];

    if (!value) {
      errors.push(`${key} environment variable is required`);
    } else if (value.length < minLength) {
      errors.push(`${key} must be at least ${minLength} characters`);
    }
  }

  // Check for default/weak secrets in production
  if (process.env.NODE_ENV === 'production') {
    if (process.env.JWT_SECRET?.includes('change') ||
        process.env.JWT_SECRET?.includes('secret') ||
        process.env.JWT_SECRET?.includes('default')) {
      errors.push('JWT_SECRET appears to be a default value - use a secure random string in production');
    }
  }

  if (errors.length > 0) {
    console.error('\n❌ Security configuration errors:');
    errors.forEach(e => console.error(`   - ${e}`));
    console.error('\n');

    if (process.env.NODE_ENV === 'production') {
      console.error('FATAL: Cannot start in production with invalid security configuration\n');
      process.exit(1);
    } else {
      console.warn('WARNING: Running in development mode with insecure configuration\n');
    }
  }
}

// ============================================
// INPUT SANITIZATION
// ============================================

/**
 * Sanitize a plain text string (names, titles, etc.)
 * Removes HTML tags and dangerous characters
 */
export function sanitizePlainText(input, maxLength = 1000) {
  if (input === null || input === undefined) {
    return input;
  }

  if (typeof input !== 'string') {
    return String(input);
  }

  return input
    // Remove HTML tags
    .replace(/<[^>]*>/g, '')
    // Remove null bytes (can cause issues)
    .replace(/\0/g, '')
    // Normalize whitespace
    .replace(/\s+/g, ' ')
    .trim()
    .substring(0, maxLength);
}

/**
 * Sanitize markdown/rich text content (descriptions, readmes)
 * Allows markdown but removes dangerous HTML and scripts
 */
export function sanitizeMarkdown(input, maxLength = 50000) {
  if (input === null || input === undefined) {
    return input;
  }

  if (typeof input !== 'string') {
    return String(input);
  }

  return input
    // Remove script tags and their content
    .replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, '')
    // Remove event handlers (onclick, onerror, etc.)
    .replace(/\s*on\w+\s*=\s*["'][^"']*["']/gi, '')
    .replace(/\s*on\w+\s*=\s*[^\s>]*/gi, '')
    // Remove dangerous URL schemes
    .replace(/javascript\s*:/gi, '')
    .replace(/vbscript\s*:/gi, '')
    .replace(/data\s*:\s*text\/html/gi, '')
    .replace(/data\s*:\s*application\/javascript/gi, '')
    .replace(/data\s*:\s*image\/svg\+xml/gi, '') // SVG can contain scripts
    // Remove style tags with expressions
    .replace(/<style\b[^<]*(?:(?!<\/style>)<[^<]*)*<\/style>/gi, '')
    // Remove dangerous HTML tags
    .replace(/<script\b[^>]*>/gi, '')
    .replace(/<\/script>/gi, '')
    .replace(/<iframe\b[^>]*>.*?<\/iframe>/gi, '')
    .replace(/<iframe\b[^>]*>/gi, '')
    .replace(/<embed\b[^>]*>/gi, '')
    .replace(/<object\b[^>]*>.*?<\/object>/gi, '')
    .replace(/<object\b[^>]*>/gi, '')
    .replace(/<form\b[^>]*>.*?<\/form>/gi, '')
    .replace(/<form\b[^>]*>/gi, '')
    .replace(/<base\b[^>]*>/gi, '') // Can redirect all URLs
    .replace(/<meta\b[^>]*http-equiv[^>]*>/gi, '') // Can redirect/refresh
    .replace(/<link\b[^>]*rel\s*=\s*["']?import["']?[^>]*>/gi, '') // HTML imports
    .replace(/<svg\b[^>]*>.*?<\/svg>/gis, '') // SVG can contain scripts
    // Remove null bytes and other dangerous characters
    .replace(/\0/g, '')
    .replace(/\x00/g, '')
    .trim()
    .substring(0, maxLength);
}

/**
 * Sanitize an array of tags
 */
export function sanitizeTags(tags, maxTags = 20, maxTagLength = 50) {
  if (!tags) {
    return [];
  }

  // Convert to array if string
  const tagArray = Array.isArray(tags)
    ? tags
    : tags.split(',').map(t => t.trim());

  return tagArray
    .slice(0, maxTags)
    .map(tag => sanitizePlainText(tag, maxTagLength).toLowerCase())
    .filter(tag => tag.length > 0);
}

// ============================================
// EXPORTS
// ============================================

export default {
  requestId,
  securityHeaders,
  httpsRedirect,
  loginLimiter,
  registerLimiter,
  oauthLimiter,
  tokenLimiter,
  uploadLimiter,
  versionLimiter,
  downloadLimiter,
  shareLimiter,
  passwordChangeLimiter,
  generalLimiter,
  logAuditEvent,
  logSecurityEvent,
  logAuthEvent,
  logAssetEvent,
  logAdminEvent,
  RESERVED_USERNAMES,
  isReservedUsername,
  validateSecrets,
  sanitizePlainText,
  sanitizeMarkdown,
  sanitizeTags,
};
