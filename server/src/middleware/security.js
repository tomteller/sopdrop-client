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

// Login: Strict limit to prevent brute force
export const loginLimiter = rateLimit({
  windowMs: 15 * 60 * 1000, // 15 minutes
  max: 5, // 5 attempts
  message: rateLimitMessage,
  standardHeaders: true,
  legacyHeaders: false,
  handler: rateLimitHandler,
  keyGenerator: (req) => req.ip, // Rate limit by IP
  skip: skipForAdmins,
  validate: false,
});

// Registration: Prevent mass account creation
export const registerLimiter = rateLimit({
  windowMs: 60 * 60 * 1000, // 1 hour
  max: 3, // 3 accounts
  message: rateLimitMessage,
  standardHeaders: true,
  legacyHeaders: false,
  handler: rateLimitHandler,
  keyGenerator: (req) => req.ip,
  skip: skipForAdmins,
  validate: false,
});

// OAuth: Handles both login and registration in one endpoint
export const oauthLimiter = rateLimit({
  windowMs: 15 * 60 * 1000, // 15 minutes
  max: 15, // 15 attempts (generous — OAuth callbacks are automated)
  message: rateLimitMessage,
  standardHeaders: true,
  legacyHeaders: false,
  handler: rateLimitHandler,
  keyGenerator: (req) => req.ip,
  skip: skipForAdmins,
  validate: false,
});

// Token generation: Prevent token farming
export const tokenLimiter = rateLimit({
  windowMs: 60 * 60 * 1000, // 1 hour
  max: 5, // 5 tokens per hour
  message: rateLimitMessage,
  standardHeaders: true,
  legacyHeaders: false,
  handler: rateLimitHandler,
  keyGenerator: (req) => req.user?.id || req.ip,
  skip: skipForAdmins,
  validate: false,
});

// Asset upload: Prevent storage abuse
export const uploadLimiter = rateLimit({
  windowMs: 60 * 60 * 1000, // 1 hour
  max: 10, // 10 uploads
  message: rateLimitMessage,
  standardHeaders: true,
  legacyHeaders: false,
  handler: rateLimitHandler,
  keyGenerator: (req) => req.user?.id || req.ip,
  skip: skipForAdmins,
  validate: false,
});

// Version publish: Allow more than new assets
export const versionLimiter = rateLimit({
  windowMs: 60 * 60 * 1000, // 1 hour
  max: 20, // 20 versions
  message: rateLimitMessage,
  standardHeaders: true,
  legacyHeaders: false,
  handler: rateLimitHandler,
  keyGenerator: (req) => req.user?.id || req.ip,
  skip: skipForAdmins,
  validate: false,
});

// Downloads: Prevent scraping
export const downloadLimiter = rateLimit({
  windowMs: 60 * 1000, // 1 minute
  max: 60, // 60 downloads per minute
  message: rateLimitMessage,
  standardHeaders: true,
  legacyHeaders: false,
  handler: rateLimitHandler,
  keyGenerator: (req) => req.ip,
  skip: skipForAdmins,
  validate: false,
});

// Temporary shares: Prevent abuse
export const shareLimiter = rateLimit({
  windowMs: 60 * 60 * 1000, // 1 hour
  max: 10, // 10 shares
  message: rateLimitMessage,
  standardHeaders: true,
  legacyHeaders: false,
  handler: rateLimitHandler,
  keyGenerator: (req) => req.user?.id || req.ip,
  skip: skipForAdmins,
  validate: false,
});

// Password change: Prevent abuse after account compromise
export const passwordChangeLimiter = rateLimit({
  windowMs: 60 * 60 * 1000, // 1 hour
  max: 3, // 3 changes per hour
  message: rateLimitMessage,
  standardHeaders: true,
  legacyHeaders: false,
  handler: rateLimitHandler,
  keyGenerator: (req) => req.user?.id || req.ip,
  skip: skipForAdmins,
  validate: false,
});

// General API: Fallback limit
export const generalLimiter = rateLimit({
  windowMs: 60 * 1000, // 1 minute
  max: 100, // 100 requests
  message: rateLimitMessage,
  standardHeaders: true,
  legacyHeaders: false,
  handler: rateLimitHandler,
  keyGenerator: (req) => req.ip,
  skip: skipForAdmins,
  validate: false,
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
